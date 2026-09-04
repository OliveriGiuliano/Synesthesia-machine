"""Standard MIDI File (SMF) encoding for offline MIDI exports.

The encoder writes a format-0 file with a single track. Video time is not a
tempo grid, so each interval between events is expressed with tempo meta
events that make the tick grid span exactly that interval; DAWs (Ableton
Live, Logic, ...) therefore place every note at its true video time.
"""

from __future__ import annotations

from dataclasses import dataclass

__TICKS_PER_QUARTER = 960
__MIN_TEMPO_US = 31
__MAX_TEMPO_US = 1_677_215


@dataclass(frozen=True, slots=True)
class MidiExportEvent:
    """One MIDI event on the absolute video-time axis."""

    time_s: float
    kind: str  # "note_on" | "note_off"
    channel: int
    note: int
    velocity: int

    def __post_init__(self) -> None:
        if self.kind not in {"note_on", "note_off"}:
            raise ValueError("kind must be 'note_on' or 'note_off'")
        if not 0 <= self.channel <= 15:
            raise ValueError("channel must be in 0..15")
        if not 0 <= self.note <= 127:
            raise ValueError("note must be in 0..127")
        if not 0 <= self.velocity <= 127:
            raise ValueError("velocity must be in 0..127")


def _variable_length(value: int) -> bytes:
    """Encode a delta time as a MIDI variable-length quantity."""

    if value < 0:
        raise ValueError("delta times cannot be negative")
    octets = bytearray([value & 0x7F])
    remaining = value >> 7
    while remaining:
        octets.insert(0, 0x80 | (remaining & 0x7F))
        remaining >>= 7
    return bytes(octets)


def encode_standard_midi_file(
    events: tuple[MidiExportEvent, ...] | list[MidiExportEvent], duration_s: float
) -> bytes:
    """Encode events as a format-0 Standard MIDI File.

    ``duration_s`` bounds the track when it exceeds the last event (an empty
    track then carries only an end-of-track meta event).
    """

    if duration_s < 0:
        raise ValueError("duration must not be negative")
    ordered = sorted(
        events, key=lambda event: (event.time_s, event.channel, event.note, event.kind)
    )
    out = bytearray()
    out += b"MThd"
    out += (6).to_bytes(4, "big")  # header length
    out += (0x0000).to_bytes(2, "big")  # format 0
    out += (1).to_bytes(2, "big")  # one track
    out += __TICKS_PER_QUARTER.to_bytes(2, "big")
    out += b"MTrk"

    track = bytearray()
    previous_time_s = 0.0
    for event in ordered:
        interval_s = max(0.0, event.time_s - previous_time_s)
        _write_interval(track, interval_s)
        previous_time_s = event.time_s
        track += _closing_delta(interval_s)
        status = 0x90 if event.kind == "note_on" else 0x80
        velocity = event.velocity if event.kind == "note_on" else 0
        track += bytes((status | event.channel, event.note, velocity))
    tail_s = max(0.0, duration_s - previous_time_s)
    _write_interval(track, tail_s)
    track += _closing_delta(tail_s)
    track += b"\xff\x2f\x00"  # end of track
    out += len(track).to_bytes(4, "big")
    out += track
    return bytes(out)


def _closing_delta(interval_s: float) -> bytes:
    """Delta that opens the event paired with ``_write_interval``.

    ``_write_interval`` consumes the interval as D ticks plus, when the
    interval is not evenly divisible, one extra remainder tick; the closing
    delta is therefore 1 tick in the remainder case and the full D ticks
    otherwise (0 ticks for simultaneous events).
    """

    if interval_s <= 0.0:
        return b"\x00"
    interval_us = round(interval_s * 1_000_000)
    if interval_us < __MIN_TEMPO_US:
        return b"\x00"
    ticks = max(1, -(-interval_us * __TICKS_PER_QUARTER // __MAX_TEMPO_US))
    if divmod(interval_us, ticks)[1]:
        return b"\x01"
    return _variable_length(ticks)


def _write_tempo(track: bytearray, tempo_us_per_quarter: int) -> None:
    track += bytes((0xFF, 0x51, 0x03))
    track += tempo_us_per_quarter.to_bytes(3, "big")


def _write_interval(track: bytearray, interval_s: float) -> None:
    """Emit the tempo change and delta ticks that elapse ``interval_s``.

    A DAW advances time by the delta ticks at the tempo in force *before* any
    meta event it reads, so the tempo meta for the interval is written first
    (at the previous event's time) and only then the ticks that elapse it.
    Intervals shorter than the minimum tempo step are treated as simultaneous
    (delta 0).
    """

    # The paired closing delta (written by the call site) already opens the
    # note event with a zero delta for simultaneous events, so this helper
    # only emits the tempo segments of a non-zero interval. A delta may only
    # open an event, so the segments are separate events: a delta-0 event
    # carries the first tempo change and a delta-ticks event carries any
    # second tempo change.
    if interval_s <= 0.0:
        return
    interval_us = round(interval_s * 1_000_000)
    if interval_us < __MIN_TEMPO_US:
        return
    ticks = max(1, -(-interval_us * __TICKS_PER_QUARTER // __MAX_TEMPO_US))
    quotient, remainder = divmod(interval_us, ticks)
    track += b"\x00"
    _write_tempo(track, quotient * __TICKS_PER_QUARTER)
    if remainder:
        track += _variable_length(ticks)
        _write_tempo(track, remainder * __TICKS_PER_QUARTER)
