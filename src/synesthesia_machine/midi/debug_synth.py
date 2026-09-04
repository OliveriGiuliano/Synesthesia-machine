"""Callback-driven diagnostic synthesizer with immutable desired-note snapshots."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray

from synesthesia_machine.contracts import DeviceDescriptor, DeviceKind, MidiStateFrame


class SynthWaveform(StrEnum):
    SINE = "SINE"
    TRIANGLE = "TRIANGLE"
    SQUARE = "SQUARE"


@dataclass(frozen=True, slots=True)
class SynthConfiguration:
    waveform: SynthWaveform = SynthWaveform.SINE
    volume: float = 0.15
    attack_ms: float = 10.0
    release_ms: float = 80.0
    max_voices: int = 32
    output_device: str = ""
    sample_rate: int = 48_000
    block_size: int = 256

    def __post_init__(self) -> None:
        if not 0.0 <= self.volume <= 1.0:
            raise ValueError("volume must be in the range 0..1")
        if self.attack_ms < 0.0 or self.release_ms < 0.0:
            raise ValueError("attack and release must not be negative")
        if not 1 <= self.max_voices <= 128:
            raise ValueError("max_voices must be in the range 1..128")
        if self.sample_rate <= 0 or self.block_size <= 0:
            raise ValueError("sample_rate and block_size must be positive")


@dataclass(frozen=True, slots=True)
class _DesiredNote:
    key: int
    note: int
    velocity: int


@dataclass(frozen=True, slots=True)
class _DesiredSnapshot:
    notes: tuple[_DesiredNote, ...] = ()
    panic_generation: int = 0


AudioCallback = Callable[[NDArray[np.float32], int, object, object], None]


class AudioOutputStream(Protocol):
    def start(self) -> object: ...

    def abort(self, ignore_errors: bool = True) -> object: ...

    def close(self, ignore_errors: bool = True) -> object: ...


type AudioStreamFactory = Callable[[SynthConfiguration, AudioCallback], AudioOutputStream]


def open_sounddevice_stream(
    configuration: SynthConfiguration, callback: AudioCallback
) -> AudioOutputStream:
    """Create, but do not start, the configured PortAudio output stream."""

    device_id = configuration.output_device
    if device_id.startswith("sounddevice:"):
        try:
            device: int | str | None = int(device_id.removeprefix("sounddevice:"))
        except ValueError as error:
            raise ValueError(f"Invalid audio output device ID: {device_id!r}") from error
    else:
        # Continue to accept the device names saved by earlier versions.
        device = device_id or None
    stream = sd.OutputStream(
        samplerate=configuration.sample_rate,
        blocksize=configuration.block_size,
        device=device,
        channels=2,
        dtype="float32",
        latency="low",
        callback=callback,
    )
    return cast(AudioOutputStream, stream)


def enumerate_audio_output_devices() -> tuple[DeviceDescriptor, ...]:
    """Return stable sounddevice indexes with labels, plus the system default choice."""

    raw_devices = cast(
        object,
        sd.query_devices(),  # pyright: ignore[reportUnknownMemberType]
    )
    if not isinstance(raw_devices, Sequence):
        raise RuntimeError("sounddevice returned an invalid device catalogue")
    devices = [DeviceDescriptor(DeviceKind.AUDIO_OUTPUT, "", "System default audio output", True)]
    for index, raw_device in enumerate(cast(Sequence[object], raw_devices)):
        if not isinstance(raw_device, Mapping):
            continue
        device_data = cast(Mapping[object, object], raw_device)
        channels = device_data.get("max_output_channels")
        name = device_data.get("name")
        display_name = " ".join(name.split()) if isinstance(name, str) else ""
        if (
            isinstance(channels, (int, float))
            and not isinstance(channels, bool)
            and channels > 0
            and display_name
        ):
            devices.append(
                DeviceDescriptor(
                    DeviceKind.AUDIO_OUTPUT,
                    f"sounddevice:{index}",
                    display_name,
                )
            )
    return tuple(sorted(devices))


class DebugSynth:
    """Fixed-voice synth with immutable writer snapshots and preallocated callback buffers.

    Graph updates, panic, and close are minimally serialized by ``_publisher_lock``.
    The PortAudio callback never takes that lock: it captures one immutable snapshot,
    touches no graph objects, and reuses the arrays allocated during construction.
    """

    _INACTIVE = np.uint8(0)
    _ATTACK = np.uint8(1)
    _SUSTAIN = np.uint8(2)
    _RELEASE = np.uint8(3)

    def __init__(
        self,
        configuration: SynthConfiguration,
        *,
        stream_factory: AudioStreamFactory = open_sounddevice_stream,
    ) -> None:
        self.configuration = configuration
        voice_count = configuration.max_voices
        block_size = configuration.block_size
        self._keys = np.full(voice_count, -1, dtype=np.int16)
        self._notes = np.zeros(voice_count, dtype=np.int16)
        self._velocities = np.zeros(voice_count, dtype=np.float32)
        self._phases = np.zeros(voice_count, dtype=np.float64)
        self._envelopes = np.zeros(voice_count, dtype=np.float32)
        self._stages = np.zeros(voice_count, dtype=np.uint8)
        self._ages = np.zeros(voice_count, dtype=np.int64)
        self._age_counter = 0
        self._sample_offsets = np.arange(block_size, dtype=np.float32)
        self._phase_buffer = np.empty(block_size, dtype=np.float32)
        self._wave_buffer = np.empty(block_size, dtype=np.float32)
        self._envelope_buffer = np.empty(block_size, dtype=np.float32)
        self._mono_buffer = np.empty(block_size, dtype=np.float32)
        self._publisher_lock = threading.Lock()
        self._desired = _DesiredSnapshot()
        self._seen_panic_generation = 0
        self._closed = False
        self._stream = stream_factory(configuration, self.callback)
        try:
            self._stream.start()
        except Exception:
            with suppress(Exception):
                self._stream.abort()
            with suppress(Exception):
                self._stream.close()
            raise

    @property
    def active_voice_count(self) -> int:
        return int(np.count_nonzero(self._stages))

    @property
    def active_keys(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                int(self._keys[index])
                for index in range(len(self._keys))
                if self._stages[index] != self._INACTIVE
            )
        )

    def update(self, state: MidiStateFrame) -> None:
        """Publish one immutable, voice-capped desired state from the graph thread."""

        with self._publisher_lock:
            if self._closed:
                return
            ranked = sorted(
                (
                    _DesiredNote(key.channel * 128 + key.note, key.note, velocity)
                    for key, velocity in state.notes.items()
                ),
                key=lambda item: (-item.velocity, item.key),
            )[: self.configuration.max_voices]
            self._desired = _DesiredSnapshot(
                tuple(sorted(ranked, key=lambda item: item.key)),
                self._desired.panic_generation,
            )

    def panic(self) -> None:
        with self._publisher_lock:
            snapshot = self._desired
            self._desired = _DesiredSnapshot(panic_generation=snapshot.panic_generation + 1)

    def close(self) -> None:
        with self._publisher_lock:
            if self._closed:
                return
            snapshot = self._desired
            self._desired = _DesiredSnapshot(panic_generation=snapshot.panic_generation + 1)
            self._closed = True
        try:
            self._stream.abort()
        finally:
            self._stream.close()

    def callback(
        self,
        outdata: NDArray[np.float32],
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """Render one stereo block without locking, logging, or graph access."""

        del time_info, status
        outdata.fill(0.0)
        if self._closed or frames != self.configuration.block_size:
            return
        if outdata.ndim != 2 or outdata.shape != (frames, 2):
            return
        desired = self._desired
        if self._seen_panic_generation != desired.panic_generation:
            self._clear_voices()
            self._seen_panic_generation = desired.panic_generation
            return

        self._reconcile(desired)
        mono = self._mono_buffer
        mono.fill(0.0)
        active_voices = 0
        for voice in range(len(self._keys)):
            if self._stages[voice] != self._INACTIVE:
                active_voices += 1
        normalization = self.configuration.volume / math.sqrt(max(1, active_voices))
        for voice in range(len(self._keys)):
            if self._stages[voice] == self._INACTIVE:
                continue
            self._render_voice(voice, normalization, mono)
        np.clip(mono, -0.95, 0.95, out=mono)
        for sample in range(frames):
            value = mono[sample]
            outdata[sample, 0] = value
            outdata[sample, 1] = value

    def _reconcile(self, desired: _DesiredSnapshot) -> None:
        for voice in range(len(self._keys)):
            if self._stages[voice] == self._INACTIVE:
                continue
            velocity = _desired_velocity(desired, int(self._keys[voice]))
            if velocity == 0:
                self._stages[voice] = self._RELEASE
            else:
                self._velocities[voice] = velocity / 127.0
                if self._stages[voice] == self._RELEASE:
                    self._stages[voice] = self._ATTACK

        for note in desired.notes:
            if self._find_voice(note.key) is not None:
                continue
            voice = self._select_voice(desired)
            self._age_counter += 1
            self._keys[voice] = note.key
            self._notes[voice] = note.note
            self._velocities[voice] = note.velocity / 127.0
            self._phases[voice] = 0.0
            self._envelopes[voice] = 0.0
            self._stages[voice] = self._ATTACK
            self._ages[voice] = self._age_counter

    def _find_voice(self, key: int) -> int | None:
        for voice in range(len(self._keys)):
            if self._stages[voice] != self._INACTIVE and int(self._keys[voice]) == key:
                return voice
        return None

    def _select_voice(self, desired: _DesiredSnapshot) -> int:
        selected: int | None = None
        for voice in range(len(self._keys)):
            if self._stages[voice] == self._INACTIVE:
                return voice
            if _desired_velocity(desired, int(self._keys[voice])) != 0:
                continue
            if selected is None:
                selected = voice
                continue
            selected_envelope = float(self._envelopes[selected])
            envelope = float(self._envelopes[voice])
            if envelope < selected_envelope or (
                envelope == selected_envelope and self._ages[voice] < self._ages[selected]
            ):
                selected = voice
        if selected is None:
            raise RuntimeError("No replaceable synth voice is available")
        return selected

    def _render_voice(self, voice: int, normalization: float, mono: NDArray[np.float32]) -> None:
        note = int(self._notes[voice])
        frequency = 440.0 * (2.0 ** ((note - 69) / 12.0))
        phase_step = 2.0 * math.pi * frequency / self.configuration.sample_rate
        phases = self._phase_buffer
        wave = self._wave_buffer
        np.multiply(self._sample_offsets, phase_step, out=phases)
        np.add(phases, self._phases[voice], out=phases)
        np.sin(phases, out=wave)
        if self.configuration.waveform is SynthWaveform.TRIANGLE:
            np.arcsin(wave, out=wave)
            np.multiply(wave, 2.0 / math.pi, out=wave)
        elif self.configuration.waveform is SynthWaveform.SQUARE:
            np.sign(wave, out=wave)

        envelope = self._envelope_buffer
        envelope.fill(0.0)
        level = float(self._envelopes[voice])
        stage = self._stages[voice]
        attack_samples = max(
            1.0, self.configuration.attack_ms * self.configuration.sample_rate / 1000
        )
        release_samples = max(
            1.0, self.configuration.release_ms * self.configuration.sample_rate / 1000
        )
        for sample in range(self.configuration.block_size):
            if stage == self._ATTACK:
                level = min(1.0, level + 1.0 / attack_samples)
                if level >= 1.0:
                    stage = self._SUSTAIN
            elif stage == self._RELEASE:
                level = max(0.0, level - 1.0 / release_samples)
                if level <= 0.0:
                    stage = self._INACTIVE
            envelope[sample] = level
            if stage == self._INACTIVE:
                break
        self._envelopes[voice] = level
        self._stages[voice] = stage
        if stage == self._INACTIVE:
            self._keys[voice] = -1
        self._phases[voice] = (self._phases[voice] + self.configuration.block_size * phase_step) % (
            2.0 * math.pi
        )
        np.multiply(wave, envelope, out=wave)
        np.multiply(wave, float(self._velocities[voice]) * normalization, out=wave)
        np.add(mono, wave, out=mono)

    def _clear_voices(self) -> None:
        self._keys.fill(-1)
        self._velocities.fill(0.0)
        self._phases.fill(0.0)
        self._envelopes.fill(0.0)
        self._stages.fill(self._INACTIVE)


def _desired_velocity(snapshot: _DesiredSnapshot, key: int) -> int:
    for note in snapshot.notes:
        if note.key == key:
            return note.velocity
    return 0


class DebugSynthService(Protocol):
    def update(self, state: MidiStateFrame) -> None: ...

    def panic(self) -> None: ...

    def close(self) -> None: ...


class NullDebugSynth:
    """Audio-free stand-in for offline exports: renders nothing, plays nothing."""

    def __init__(self, configuration: SynthConfiguration) -> None:
        del configuration

    def update(self, state: MidiStateFrame) -> None:
        del state

    def panic(self) -> None:
        pass

    def close(self) -> None:
        pass


type DebugSynthFactory = Callable[[SynthConfiguration], DebugSynthService]


__all__ = [
    "AudioCallback",
    "AudioOutputStream",
    "AudioStreamFactory",
    "DebugSynth",
    "DebugSynthFactory",
    "DebugSynthService",
    "SynthConfiguration",
    "SynthWaveform",
    "enumerate_audio_output_devices",
    "open_sounddevice_stream",
]
