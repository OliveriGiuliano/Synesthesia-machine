"""Deterministic desired-state MIDI transformation and merge utilities."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ResetReason,
    VariadicInputSpec,
)

MULTIPLY_VELOCITY_TYPE_ID = "synmachine.utility.multiply_velocity"
TRANSPOSE_TYPE_ID = "synmachine.utility.transpose"
MIDI_MERGE_TYPE_ID = "synmachine.utility.midi_merge"


class _MidiRuntimeBase:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class MultiplyVelocityRuntime(_MidiRuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        state = _midi_state(inputs["midi"])
        factor = _number(inputs.get("factor", parameters["factor"]))
        try:
            return {"midi": multiply_velocity(state, factor, self.node_id, context)}
        except ValueError as error:
            raise ExpectedNodeError("invalid_velocity_factor", str(error)) from error


class TransposeRuntime(_MidiRuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        state = _midi_state(inputs["midi"])
        semitones = _integer(inputs.get("semitones", parameters["semitones"]))
        try:
            return {"midi": transpose_midi_state(state, semitones, self.node_id, context)}
        except ValueError as error:
            raise ExpectedNodeError("invalid_transpose", str(error)) from error


class MidiMergeRuntime(_MidiRuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters
        try:
            states = tuple(_midi_state(value) for value in inputs.values())
            return {"midi": merge_midi_states(states, self.node_id, context)}
        except ValueError as error:
            raise ExpectedNodeError("invalid_midi_merge", str(error)) from error


def multiply_velocity(
    state: MidiStateFrame,
    factor: float,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Scale active velocities with deterministic half-up rounding and MIDI clamping."""

    _validate_input_clock(state, context)
    if not math.isfinite(factor) or factor < 0.0:
        raise ValueError("Velocity factor must be finite and non-negative")
    if factor == 0.0:
        return MidiStateFrame({}, context, node_id)
    notes: dict[MidiNoteKey, int] = {}
    for key, velocity in state.notes.items():
        scaled = velocity * factor
        notes[key] = 127 if scaled >= 127.0 else max(1, math.floor(scaled + 0.5))
    return MidiStateFrame(dict(sorted(notes.items())), context, node_id)


def transpose_midi_state(
    state: MidiStateFrame,
    semitones: int,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Transpose desired notes and discard results outside the MIDI note range."""

    _validate_input_clock(state, context)
    notes = {
        MidiNoteKey(key.channel, transposed): velocity
        for key, velocity in state.notes.items()
        if 0 <= (transposed := key.note + semitones) <= 127
    }
    return MidiStateFrame(dict(sorted(notes.items())), context, node_id)


def merge_midi_states(
    states: Iterable[MidiStateFrame],
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Merge same-clock states, keeping maximum velocity for duplicate note/channel keys."""

    inputs = tuple(states)
    if len(inputs) < 2:
        raise ValueError("MIDI Merge requires at least two input states")
    notes: dict[MidiNoteKey, int] = {}
    for state in inputs:
        _validate_input_clock(state, context)
        for key, velocity in state.notes.items():
            notes[key] = max(notes.get(key, 0), velocity)
    return MidiStateFrame(dict(sorted(notes.items())), context, node_id)


def create_midi_utility_definitions() -> tuple[NodeDefinition, ...]:
    midi_input = (InputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),)
    midi_output = (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),)
    return (
        NodeDefinition(
            MULTIPLY_VELOCITY_TYPE_ID,
            1,
            "Multiply Velocity",
            "Utility / MIDI",
            "Scale every active MIDI velocity without emitting raw messages.",
            midi_input,
            midi_output,
            (
                ParameterSpec(
                    "factor",
                    "Factor",
                    PortType.FLOAT,
                    1.0,
                    minimum=0.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                ),
            ),
            ExecutionKind.STATELESS,
            MultiplyVelocityRuntime,
            aliases=("velocity scale", "midi gain", "scale velocity"),
            parameter_validator=_validate_multiply_parameters,
        ),
        NodeDefinition(
            TRANSPOSE_TYPE_ID,
            1,
            "Transpose",
            "Utility / MIDI",
            "Shift MIDI notes by semitones and discard out-of-range results.",
            midi_input,
            midi_output,
            (
                ParameterSpec(
                    "semitones",
                    "Semitones",
                    PortType.INT,
                    0,
                    minimum=-127,
                    maximum=127,
                    connectable=True,
                    connected_port_type=PortType.INT,
                ),
            ),
            ExecutionKind.STATELESS,
            TransposeRuntime,
            aliases=("Pitch Up or Down", "pitch shift", "semitone shift"),
        ),
        NodeDefinition(
            MIDI_MERGE_TYPE_ID,
            1,
            "MIDI Merge",
            "Utility / MIDI",
            "Merge two or more same-clock desired MIDI states by maximum velocity.",
            (),
            midi_output,
            (),
            ExecutionKind.STATELESS,
            MidiMergeRuntime,
            aliases=("combine midi", "mix midi", "many to one midi"),
            variadic_input=VariadicInputSpec(
                "midi", "MIDI state", PortType.MIDI_STATE, minimum_count=2
            ),
        ),
    )


def _validate_multiply_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    factor = _number(parameters["factor"])
    return () if math.isfinite(factor) else ("Velocity factor must be finite",)


def _validate_input_clock(state: MidiStateFrame, context: FrameContext) -> None:
    if state.context.clock_id != context.clock_id:
        raise ValueError("MIDI state clock does not match the execution clock")


def _midi_state(value: object) -> MidiStateFrame:
    if isinstance(value, MidiStateFrame):
        return value
    raise TypeError(f"Expected MidiStateFrame, got {type(value).__name__}")


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected numeric value, got {type(value).__name__}")


def _integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected integer value, got {type(value).__name__}")


__all__ = [
    "MIDI_MERGE_TYPE_ID",
    "MULTIPLY_VELOCITY_TYPE_ID",
    "TRANSPOSE_TYPE_ID",
    "MidiMergeRuntime",
    "MultiplyVelocityRuntime",
    "TransposeRuntime",
    "create_midi_utility_definitions",
    "merge_midi_states",
    "multiply_velocity",
    "transpose_midi_state",
]
