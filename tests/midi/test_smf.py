"""Standard MIDI File encoder: layout and DAW-walk timing."""

from __future__ import annotations

import pytest

from synesthesia_machine.midi.smf import MidiExportEvent, encode_standard_midi_file


def parse_smf(data: bytes) -> list[tuple[float, str, int, int, int]]:
    """Walk a format-0 file like a DAW: one item per event, deltas advance at
    the tempo in force before the slot's meta events."""

    assert data[0:4] == b"MThd"
    fmt, ntrk, division = (int.from_bytes(data[i : i + 2], "big") for i in (8, 10, 12))
    assert (fmt, ntrk, division) == (0, 1, 960)
    assert data[14:18] == b"MTrk"
    body = data[22 : 22 + int.from_bytes(data[18:22], "big")]
    ticks = t_us = 0
    tempo_us = 500_000
    seq: list[tuple[float, str, int, int, int]] = []
    i, n = 0, len(body)
    while i < n:
        ticks = 0
        while True:
            byte = body[i]
            i += 1
            ticks = (ticks << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        t_us += ticks * tempo_us // 960
        if body[i] == 0xFF:
            mtype, mlen = body[i + 1], body[i + 2]
            if mtype == 0x2F:
                assert mlen == 0 and i + 3 == n
                return seq
            if mtype == 0x51:
                tempo_us = int.from_bytes(body[i + 3 : i + 6], "big")
            i += 3 + mlen
        else:
            seq.append(
                (
                    t_us / 1e6,
                    "note_on" if body[i] >> 4 == 9 else "note_off",
                    body[i] & 0x0F,
                    body[i + 1],
                    body[i + 2],
                )
            )
            i += 3
    return seq


def test_simultaneous_events_and_basic_note_layout() -> None:
    events = (
        MidiExportEvent(0.0, "note_on", 0, 60, 100),
        MidiExportEvent(0.0, "note_on", 0, 64, 90),
        MidiExportEvent(1.5, "note_off", 0, 60, 0),
    )
    got = parse_smf(encode_standard_midi_file(events, 2.0))
    assert got == [
        (0.0, "note_on", 0, 60, 100),
        (0.0, "note_on", 0, 64, 90),
        (1.5, "note_off", 0, 60, 0),
    ]


def test_long_gap_uses_multiple_ticks_with_exact_timing() -> None:
    events = (
        MidiExportEvent(0.0, "note_on", 0, 60, 100),
        MidiExportEvent(10.0, "note_on", 0, 67, 100),
        MidiExportEvent(10.00002, "note_off", 0, 67, 0),  # sub-31us: simultaneous
    )
    got = parse_smf(encode_standard_midi_file(events, 12.0))
    assert [e[1] for e in got] == ["note_on", "note_on", "note_off"]
    for actual, expected in zip((e[0] for e in got), (0.0, 10.0, 10.0), strict=True):
        assert abs(actual - expected) < 0.0005


def test_three_fps_grid_stays_exact_across_many_events() -> None:
    events = tuple(MidiExportEvent(k / 30, "note_on", 0, 60, 100) for k in range(90))
    got = parse_smf(encode_standard_midi_file(events, 3.0))
    assert len(got) == 90
    for k, (t, kind, channel, note, velocity) in enumerate(got):
        assert kind == "note_on" and (channel, note, velocity) == (0, 60, 100)
        assert abs(t - k / 30) < 0.0005


def test_empty_export_is_a_valid_file_and_tail_is_honored() -> None:
    assert parse_smf(encode_standard_midi_file((), 0.0)) == []
    got = parse_smf(encode_standard_midi_file((MidiExportEvent(0.0, "note_on", 0, 60, 100),), 30.0))
    assert len(got) == 1 and abs(got[0][0]) < 0.0005


def test_events_are_sorted_and_channel_velocity_are_preserved() -> None:
    events = (
        MidiExportEvent(0.5, "note_on", 15, 127, 127),
        MidiExportEvent(0.1, "note_on", 0, 0, 1),
        MidiExportEvent(0.1, "note_off", 7, 50, 0),
    )
    got = parse_smf(encode_standard_midi_file(events, 1.0))
    assert [e[0] for e in got] == [0.1, 0.1, 0.5]
    assert {e[2:] for e in got} == {(0, 0, 1), (7, 50, 0), (15, 127, 127)}
    last = got[-1]
    assert last[1] == "note_on" and last[2:] == (15, 127, 127)


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        MidiExportEvent(0.0, "pitch_bend", 0, 60, 100)
    with pytest.raises(ValueError):
        MidiExportEvent(0.0, "note_on", 16, 60, 100)
    with pytest.raises(ValueError):
        MidiExportEvent(0.0, "note_on", 0, 128, 100)
    with pytest.raises(ValueError):
        encode_standard_midi_file((), -0.1)
