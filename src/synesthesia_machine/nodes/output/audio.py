"""Opt-in Generate Audio sink backed by the callback-safe diagnostic synth."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from uuid import UUID

from synesthesia_machine.contracts import (
    DeviceKind,
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
    ParameterEditorHint,
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
        self._service = _DebugAudioOutputService(synth_factory)

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        if not _boolean(parameters["enabled"]):
            self._service.disable()
            return {}
        midi = inputs["midi"]
        if midi is NoData:
            self._service.panic()
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
        error = self._service.publish(configuration, midi)
        if error is not None:
            raise ExpectedNodeError(
                "audio_output_unavailable",
                f"Could not open debug audio output: {error}",
            ) from error
        return {}

    def panic(self) -> None:
        self._service.panic()

    def reset(self, reason: ResetReason) -> None:
        del reason
        self.panic()

    def close(self) -> None:
        self._service.close()

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        """Wait for pending audio work; intended for deterministic lifecycle checks."""

        return self._service.wait_until_idle(timeout)


@dataclass(frozen=True, slots=True)
class _AudioCommand:
    generation: int
    configuration: SynthConfiguration | None
    midi: MidiStateFrame | None


class _DebugAudioOutputService:
    """Own slow audio-device operations outside the graph worker.

    Desired MIDI state is intentionally coalesced: audio is a live sink and only the latest state is
    useful. A failed configuration remains latched until the user changes it or disables the node,
    preventing an unavailable device from being reopened on every source tick.
    """

    def __init__(self, synth_factory: DebugSynthFactory) -> None:
        self._synth_factory = synth_factory
        self._condition = threading.Condition()
        self._generation = 0
        self._pending: _AudioCommand | None = None
        self._busy = False
        self._closed = False
        self._synth: DebugSynthService | None = None
        self._configuration: SynthConfiguration | None = None
        self._failed_configuration: SynthConfiguration | None = None
        self._failure: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="synmachine-debug-audio-output",
            daemon=True,
        )
        self._thread.start()

    def publish(
        self,
        configuration: SynthConfiguration,
        midi: MidiStateFrame,
    ) -> BaseException | None:
        synth: DebugSynthService | None = None
        with self._condition:
            if self._closed:
                return RuntimeError("audio output service is closed")
            if configuration == self._failed_configuration:
                return self._failure
            if self._failed_configuration is not None:
                self._failed_configuration = None
                self._failure = None
            self._generation += 1
            if (
                self._synth is not None
                and self._configuration == configuration
                and not self._busy
                and self._pending is None
            ):
                synth = self._synth
            else:
                self._pending = _AudioCommand(self._generation, configuration, midi)
                self._condition.notify()
        if synth is None:
            return None
        try:
            # Publishing a desired-note snapshot is callback-safe and does no device I/O. Slow
            # open/replace/close operations remain exclusively on the service thread.
            synth.update(midi)
        except Exception as error:
            with self._condition:
                self._failed_configuration = configuration
                self._failure = error
                self._generation += 1
                self._pending = _AudioCommand(self._generation, None, None)
                self._condition.notify()
            return error
        return None

    def disable(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._failed_configuration = None
            self._failure = None
            if self._configuration is None and self._pending is None and not self._busy:
                return
            self._generation += 1
            self._pending = _AudioCommand(self._generation, None, None)
            self._condition.notify()

    def panic(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            self._pending = None
            synth = self._synth
            self._condition.notify_all()
        if synth is not None:
            synth.panic()

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending is not None or self._busy:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._pending = None
            synth = self._synth
            self._condition.notify_all()
        if synth is not None:
            synth.panic()
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._closed:
                        self._condition.wait()
                    if self._closed:
                        return
                    command = self._pending
                    self._pending = None
                    self._busy = True
                assert command is not None
                try:
                    self._apply(command)
                finally:
                    with self._condition:
                        self._busy = False
                        self._condition.notify_all()
        finally:
            self._close_current()

    def _apply(self, command: _AudioCommand) -> None:
        if command.configuration is None:
            self._close_current()
            return
        try:
            if command.configuration != self._configuration:
                self._close_current()
                synth = self._synth_factory(command.configuration)
                with self._condition:
                    self._synth = synth
                    self._configuration = command.configuration
            else:
                synth = self._synth
            assert synth is not None
            with self._condition:
                is_current = command.generation == self._generation and not self._closed
            if not is_current:
                synth.panic()
                return
            assert command.midi is not None
            synth.update(command.midi)
        except Exception as error:
            self._close_current()
            with self._condition:
                if command.generation == self._generation and not self._closed:
                    self._failed_configuration = command.configuration
                    self._failure = error

    def _close_current(self) -> None:
        with self._condition:
            synth = self._synth
            self._synth = None
            self._configuration = None
        if synth is not None:
            try:
                synth.panic()
            finally:
                synth.close()


def create_output_definitions(
    *, synth_factory: DebugSynthFactory = DebugSynth
) -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            GENERATE_AUDIO_TYPE_ID,
            1,
            "Play MIDI as Audio",
            "Output / Audio",
            "Hear the incoming MIDI notes through a simple built-in synthesizer.",
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
                    "volume",
                    "Master volume",
                    PortType.FLOAT,
                    0.15,
                    minimum=0.0,
                    maximum=1.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "attack_ms",
                    "Attack (ms)",
                    PortType.FLOAT,
                    10.0,
                    minimum=0.0,
                    maximum=2000.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "release_ms",
                    "Release (ms)",
                    PortType.FLOAT,
                    80.0,
                    minimum=0.0,
                    maximum=5000.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "max_voices", "Maximum voices", PortType.INT, 32, minimum=1, maximum=128
                ),
                ParameterSpec(
                    "output_device",
                    "Output audio device",
                    PortType.STRING,
                    "",
                    help_text="Select a detected audio output, or use the system default.",
                    device_kind=DeviceKind.AUDIO_OUTPUT,
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
