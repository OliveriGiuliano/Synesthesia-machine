"""Opt-in Generate Audio sink backed by the callback-safe diagnostic synth."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from functools import partial
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.midi.debug_synth import (
    DebugSynth,
    DebugSynthFactory,
    DebugSynthService,
    SynthConfiguration,
    SynthWaveform,
)
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    ParameterSpec,
    ResetReason,
)

GENERATE_AUDIO_TYPE_ID = "synmachine.output.generate_audio"


class GenerateAudioRuntime:
    def __init__(
        self,
        node_id: UUID,
        *,
        synth_factory: DebugSynthFactory = DebugSynth,
    ) -> None:
        self.node_id = node_id
        self._lock = threading.RLock()
        self._synth_factory = synth_factory
        self._synth: DebugSynthService | None = None
        self._configuration: SynthConfiguration | None = None

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        with self._lock:
            if not _boolean(parameters["enabled"]):
                self._close_synth_locked()
                return {}
            midi = inputs["midi"]
            if midi is NoData:
                self._panic_locked()
                return {}
            if not isinstance(midi, MidiStateFrame):
                raise TypeError("Generate Audio requires MIDI state input")
            configuration = SynthConfiguration(
                waveform=SynthWaveform(_text(parameters["waveform"])),
                volume=_number(parameters["volume"]),
                attack_ms=_number(parameters["attack_ms"]),
                release_ms=_number(parameters["release_ms"]),
                max_voices=_integer(parameters["max_voices"]),
                output_device=_text(parameters["output_device"]),
            )
            if self._synth is None or configuration != self._configuration:
                self._close_synth_locked()
                try:
                    self._synth = self._synth_factory(configuration)
                except Exception as error:
                    raise ExpectedNodeError(
                        "audio_output_unavailable",
                        f"Could not open debug audio output: {error}",
                    ) from error
                self._configuration = configuration
            self._synth.update(midi)
            return {}

    def panic(self) -> None:
        with self._lock:
            self._panic_locked()

    def reset(self, reason: ResetReason) -> None:
        del reason
        self.panic()

    def close(self) -> None:
        with self._lock:
            self._close_synth_locked()

    def _panic_locked(self) -> None:
        if self._synth is not None:
            self._synth.panic()

    def _close_synth_locked(self) -> None:
        synth = self._synth
        self._synth = None
        self._configuration = None
        if synth is not None:
            synth.close()


def create_output_definitions(
    *, synth_factory: DebugSynthFactory = DebugSynth
) -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            GENERATE_AUDIO_TYPE_ID,
            1,
            "Generate Audio",
            "Output / Audio",
            "Opt-in diagnostic polyphonic synth driven by complete desired MIDI state.",
            (InputPortSpec("midi", "MIDI State", PortType.MIDI_STATE),),
            (),
            (
                ParameterSpec("enabled", "Enable audio output", PortType.BOOL, False),
                ParameterSpec(
                    "waveform",
                    "Waveform",
                    PortType.STRING,
                    SynthWaveform.SINE.value,
                    choices=tuple(waveform.value for waveform in SynthWaveform),
                ),
                ParameterSpec(
                    "volume", "Master volume", PortType.FLOAT, 0.15, minimum=0.0, maximum=1.0
                ),
                ParameterSpec(
                    "attack_ms", "Attack (ms)", PortType.FLOAT, 10.0, minimum=0.0, maximum=2000.0
                ),
                ParameterSpec(
                    "release_ms", "Release (ms)", PortType.FLOAT, 80.0, minimum=0.0, maximum=5000.0
                ),
                ParameterSpec(
                    "max_voices", "Maximum voices", PortType.INT, 32, minimum=1, maximum=128
                ),
                ParameterSpec(
                    "output_device",
                    "Output audio device",
                    PortType.STRING,
                    "",
                    help_text="Empty uses the system default output device.",
                ),
            ),
            ExecutionKind.SINK,
            partial(GenerateAudioRuntime, synth_factory=synth_factory),
            cache_policy=CachePolicy.NEVER,
            handles_no_data=True,
            aliases=("debug synth", "synthesizer", "sound"),
        ),
    )


def _boolean(value: ParameterValue) -> bool:
    if not isinstance(value, bool):
        raise TypeError("Expected bool parameter")
    return value


def _integer(value: ParameterValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("Expected integer parameter")
    return value


def _number(value: ParameterValue) -> float:
    if not isinstance(value, float):
        raise TypeError("Expected float parameter")
    return value


def _text(value: ParameterValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected text parameter")
    return value


__all__ = ["GENERATE_AUDIO_TYPE_ID", "GenerateAudioRuntime", "create_output_definitions"]
