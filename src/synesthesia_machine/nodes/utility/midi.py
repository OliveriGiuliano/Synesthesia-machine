"""Deterministic desired-state MIDI transformation and merge utilities."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import cast
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
    InputPortSpec,
    NodeDefinition,
    NodeExecutionContract,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterSpec,
    PureFunctionRuntime,
    VariadicInputSpec,
)

MULTIPLY_VELOCITY_TYPE_ID = "synmachine.utility.multiply_velocity"
TRANSPOSE_TYPE_ID = "synmachine.utility.transpose"
MIDI_MERGE_TYPE_ID = "synmachine.utility.midi_merge"


def _multiply_velocity_process(
    node_id: UUID,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> Mapping[str, RuntimeValue]:
    state = cast(MidiStateFrame, inputs["midi"])
    factor = cast(float, inputs.get("factor", parameters["factor"]))
    return {"midi": multiply_velocity(state, factor, node_id, context)}


class MultiplyVelocityRuntime(PureFunctionRuntime):
    def __init__(self, node_id: UUID) -> None:
        super().__init__(
            node_id, processor=_multiply_velocity_process, error_code="invalid_velocity_factor"
        )


def _transpose_process(
    node_id: UUID,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> Mapping[str, RuntimeValue]:
    state = cast(MidiStateFrame, inputs["midi"])
    semitones = cast(int, inputs.get("semitones", parameters["semitones"]))
    return {"midi": transpose_midi_state(state, semitones, node_id, context)}


class TransposeRuntime(PureFunctionRuntime):
    def __init__(self, node_id: UUID) -> None:
        super().__init__(node_id, processor=_transpose_process, error_code="invalid_transpose")


def _midi_merge_process(
    node_id: UUID,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> Mapping[str, RuntimeValue]:
    del parameters
    states = tuple(cast(MidiStateFrame, value) for value in inputs.values())
    return {"midi": merge_midi_states(states, node_id, context)}


class MidiMergeRuntime(PureFunctionRuntime):
    def __init__(self, node_id: UUID) -> None:
        super().__init__(node_id, processor=_midi_merge_process, error_code="invalid_midi_merge")


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
            execution=NodeExecutionContract(
                MULTIPLY_VELOCITY_TYPE_ID,
                1,
                ExecutionKind.STATELESS,
                midi_input,
                midi_output,
                (
                    ParameterSpec(
                        "factor",
                        "Factor",
                        PortType.FLOAT,
                        1.0,
                        help_text=(
                            "Multiplies the velocity of every note by this factor; 1 is unchanged."
                        ),
                        minimum=0.0,
                        connectable=True,
                        connected_port_type=PortType.FLOAT,
                    ),
                ),
                MultiplyVelocityRuntime,
                parameter_validator=_validate_multiply_parameters,
            ),
            presentation=NodePresentationIntent(
                "Multiply Velocity",
                "Utility / MIDI",
                "Turns the volume of every active note up or down.",
                aliases=("velocity scale", "midi gain", "scale velocity"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                TRANSPOSE_TYPE_ID,
                1,
                ExecutionKind.STATELESS,
                midi_input,
                midi_output,
                (
                    ParameterSpec(
                        "semitones",
                        "Semitones",
                        PortType.INT,
                        0,
                        help_text=(
                            "Number of semitones the note numbers shift by; negative values go "
                            "down."
                        ),
                        minimum=-127,
                        maximum=127,
                        connectable=True,
                        connected_port_type=PortType.INT,
                    ),
                ),
                TransposeRuntime,
            ),
            presentation=NodePresentationIntent(
                "Transpose",
                "Utility / MIDI",
                "Moves all the notes up or down by a number of semitones. Notes that fall outside "
                "the "
                "MIDI range are dropped.",
                aliases=("Pitch Up or Down", "pitch shift", "semitone shift"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                MIDI_MERGE_TYPE_ID,
                1,
                ExecutionKind.STATELESS,
                (),
                midi_output,
                (),
                MidiMergeRuntime,
                variadic_input=VariadicInputSpec(
                    "midi", "MIDI state", PortType.MIDI_STATE, minimum_count=2
                ),
            ),
            presentation=NodePresentationIntent(
                "MIDI Merge",
                "Utility / MIDI",
                "Combines several MIDI note streams into one. Where notes overlap, the loudest one "
                "wins.",
                aliases=("combine midi", "mix midi", "many to one midi"),
            ),
        ),
    )


def _validate_multiply_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    factor = cast(float, parameters["factor"])
    return () if math.isfinite(factor) else ("Velocity factor must be finite",)


def _validate_input_clock(state: MidiStateFrame, context: FrameContext) -> None:
    if state.context.clock_id != context.clock_id:
        raise ValueError("MIDI state clock does not match the execution clock")


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
