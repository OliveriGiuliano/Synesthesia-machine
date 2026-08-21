"""Desired-state MIDI utility algorithms, definitions, and runtime integration."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import (
    ExecutionKind,
    NodeDefinition,
    NodeRegistry,
    OutputPortSpec,
    ResetReason,
)
from synesthesia_machine.nodes.utility import (
    MIDI_MERGE_TYPE_ID,
    MULTIPLY_VELOCITY_TYPE_ID,
    TRANSPOSE_TYPE_ID,
    create_midi_utility_definitions,
    merge_midi_states,
    multiply_velocity,
    transpose_midi_state,
)
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000620")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000000621")
SOURCE_A = UUID("00000000-0000-0000-0000-000000000622")
SOURCE_B = UUID("00000000-0000-0000-0000-000000000623")
SOURCE_C = UUID("00000000-0000-0000-0000-000000000624")
UTILITY = UUID("00000000-0000-0000-0000-000000000625")


def _context(clock_id: UUID = CLOCK, tick_index: int = 1) -> FrameContext:
    return FrameContext(clock_id, tick_index, tick_index - 1, 0.0, tick_index, None, False)


def _state(
    notes: Mapping[tuple[int, int], int],
    *,
    clock_id: UUID = CLOCK,
    source_node_id: UUID = SOURCE_A,
) -> MidiStateFrame:
    return MidiStateFrame(
        {MidiNoteKey(channel, note): velocity for (channel, note), velocity in notes.items()},
        _context(clock_id),
        source_node_id,
    )


def test_multiply_velocity_rounds_half_up_clamps_and_zero_empties() -> None:
    state = _state({(2, 62): 100, (0, 60): 1, (1, 61): 3})
    scaled = multiply_velocity(state, 0.5, UTILITY, _context())
    assert scaled.notes == {
        MidiNoteKey(0, 60): 1,
        MidiNoteKey(1, 61): 2,
        MidiNoteKey(2, 62): 50,
    }
    assert tuple(scaled.notes) == tuple(sorted(scaled.notes))
    assert multiply_velocity(state, 2.0, UTILITY, _context()).notes == {
        MidiNoteKey(0, 60): 2,
        MidiNoteKey(1, 61): 6,
        MidiNoteKey(2, 62): 127,
    }
    assert multiply_velocity(state, 1e308, UTILITY, _context()).notes == {
        MidiNoteKey(0, 60): 127,
        MidiNoteKey(1, 61): 127,
        MidiNoteKey(2, 62): 127,
    }
    assert multiply_velocity(state, 0.0, UTILITY, _context()).notes == {}
    with pytest.raises(ValueError, match="finite and non-negative"):
        multiply_velocity(state, -0.1, UTILITY, _context())
    with pytest.raises(ValueError, match="finite and non-negative"):
        multiply_velocity(state, float("inf"), UTILITY, _context())


def test_transpose_preserves_channels_velocities_and_drops_out_of_range_notes() -> None:
    state = _state({(15, 127): 100, (4, 60): 80, (0, 0): 20})
    transposed = transpose_midi_state(state, 12, UTILITY, _context())
    assert transposed.notes == {
        MidiNoteKey(0, 12): 20,
        MidiNoteKey(4, 72): 80,
    }
    assert tuple(transposed.notes) == tuple(sorted(transposed.notes))
    assert transpose_midi_state(state, -12, UTILITY, _context()).notes == {
        MidiNoteKey(4, 48): 80,
        MidiNoteKey(15, 115): 100,
    }


def test_midi_merge_is_variadic_deterministic_and_uses_maximum_duplicate_velocity() -> None:
    first = _state({(0, 60): 40, (1, 64): 90}, source_node_id=SOURCE_A)
    second = _state({(0, 60): 100, (2, 67): 70}, source_node_id=SOURCE_B)
    third = _state({(0, 60): 80, (1, 64): 20}, source_node_id=SOURCE_C)
    merged = merge_midi_states((first, second, third), UTILITY, _context())
    assert merged.notes == {
        MidiNoteKey(0, 60): 100,
        MidiNoteKey(1, 64): 90,
        MidiNoteKey(2, 67): 70,
    }
    assert tuple(merged.notes) == tuple(sorted(merged.notes))
    assert merged.source_node_id == UTILITY
    with pytest.raises(ValueError, match="at least two"):
        merge_midi_states((first,), UTILITY, _context())


def test_unary_midi_utilities_reject_wrong_runtime_clock() -> None:
    state = _state({}, clock_id=OTHER_CLOCK)
    with pytest.raises(ValueError, match="clock does not match"):
        multiply_velocity(state, 1.0, UTILITY, _context())
    with pytest.raises(ValueError, match="clock does not match"):
        transpose_midi_state(state, 0, UTILITY, _context())


@pytest.mark.parametrize(
    ("type_id", "error_code"),
    [
        (MULTIPLY_VELOCITY_TYPE_ID, "invalid_velocity_factor"),
        (TRANSPOSE_TYPE_ID, "invalid_transpose"),
    ],
)
def test_unary_runtime_clock_mismatch_is_recoverable(
    type_id: str,
    error_code: str,
) -> None:
    definition = next(
        definition
        for definition in create_midi_utility_definitions()
        if definition.type_id == type_id
    )
    parameters, validation_errors = definition.parameter_values({})
    assert not validation_errors
    source_key = PortKey(SOURCE_A, "midi")
    plan = ExecutionPlan(
        UUID("00000000-0000-0000-0000-000000000626"),
        1,
        (
            CompiledNode(
                UTILITY,
                definition,
                parameters=parameters,
                input_bindings={"midi": InputBinding(source_key)},
                input_types={"midi": PortType.MIDI_STATE},
                output_types={"midi": PortType.MIDI_STATE},
                clock_id=CLOCK,
                is_static=False,
            ),
        ),
        frozenset({UTILITY}),
    )
    scheduler = Scheduler(plan)
    try:
        tick = scheduler.execute_tick(
            _context(),
            source_values={source_key: _state({}, clock_id=OTHER_CLOCK)},
        )
    finally:
        scheduler.close()

    assert tick.values[PortKey(UTILITY, "midi")] is NoData
    assert len(tick.errors) == 1
    assert tick.errors[0].code == error_code
    assert tick.errors[0].recoverable
    assert tick.invocation_counts == {UTILITY: 1}


def test_midi_merge_rejects_mixed_or_wrong_runtime_clocks() -> None:
    valid = _state({}, source_node_id=SOURCE_A)
    wrong = _state({}, clock_id=OTHER_CLOCK, source_node_id=SOURCE_B)
    with pytest.raises(ValueError, match="clock does not match"):
        merge_midi_states((valid, wrong), UTILITY, _context())


def test_midi_utility_definitions_have_stable_ports_parameters_alias_and_variadic_contract() -> (
    None
):
    definitions = create_midi_utility_definitions()
    assert tuple(definition.type_id for definition in definitions) == (
        MULTIPLY_VELOCITY_TYPE_ID,
        TRANSPOSE_TYPE_ID,
        MIDI_MERGE_TYPE_ID,
    )
    multiply, transpose, merge = definitions
    assert multiply.parameter("factor").connectable  # type: ignore[union-attr]
    assert multiply.parameter("factor").connected_port_type is PortType.FLOAT  # type: ignore[union-attr]
    assert transpose.display_name == "Transpose"
    assert "Pitch Up or Down" in transpose.aliases
    assert transpose.parameter("semitones").connectable  # type: ignore[union-attr]
    assert transpose.parameter("semitones").connected_port_type is PortType.FLOAT  # type: ignore[union-attr]
    assert merge.variadic_input is not None and merge.variadic_input.minimum_count == 2
    assert tuple(port.id for port in merge.input_ports()) == ("midi_1", "midi_2")


class _MidiSourceRuntime:
    states: Mapping[UUID, MidiStateFrame] = {}

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {"midi": self.states[self.node_id]}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def _runtime_registry() -> NodeRegistry:
    source = NodeDefinition(
        "test.midi_value_source",
        1,
        "MIDI source",
        "Test",
        "Static MIDI fixture source.",
        (),
        (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
        (),
        ExecutionKind.STATELESS,
        _MidiSourceRuntime,
    )
    return NodeRegistry((source, *create_midi_utility_definitions()))


def test_scheduler_executes_true_variadic_merge_and_nodata_propagates_without_invocation() -> None:
    _MidiSourceRuntime.states = {
        SOURCE_A: _state({(0, 60): 40}, source_node_id=SOURCE_A),
        SOURCE_B: _state({(0, 60): 100}, source_node_id=SOURCE_B),
        SOURCE_C: _state({(1, 67): 70}, source_node_id=SOURCE_C),
    }
    document = GraphDocument()
    for node_id in (SOURCE_A, SOURCE_B, SOURCE_C):
        document.add_node("test.midi_value_source", node_id=node_id)
    document.add_node(MIDI_MERGE_TYPE_ID, node_id=UTILITY)
    document.add_connection(SOURCE_A, "midi", UTILITY, "midi_1")
    document.add_connection(SOURCE_B, "midi", UTILITY, "midi_2")
    document.add_connection(SOURCE_C, "midi", UTILITY, "midi_10")
    result = GraphCompiler(_runtime_registry()).compile(document.snapshot())
    assert result.report.is_valid and result.plan is not None
    assert tuple(result.plan.node(UTILITY).input_bindings) == (  # type: ignore[union-attr]
        "midi_1",
        "midi_2",
        "midi_10",
    )

    scheduler = Scheduler(result.plan)
    nodata_scheduler = Scheduler(result.plan)
    try:
        tick = scheduler.execute_tick(_context())
        assert tick.errors == ()
        output = tick.values[PortKey(UTILITY, "midi")]
        assert isinstance(output, MidiStateFrame)
        assert output.notes == {MidiNoteKey(0, 60): 100, MidiNoteKey(1, 67): 70}

        nodata = nodata_scheduler.execute_tick(
            _context(tick_index=2),
            source_values={PortKey(SOURCE_A, "midi"): NoData},
        )
        assert nodata.values[PortKey(UTILITY, "midi")] is NoData
        assert UTILITY not in nodata.invocation_counts
    finally:
        scheduler.close()
        nodata_scheduler.close()


def test_compiler_rejects_missing_and_different_clock_variadic_merge_inputs() -> None:
    registry = create_application_registry()
    incomplete = GraphDocument()
    incomplete.add_node(MIDI_MERGE_TYPE_ID, node_id=UTILITY)
    result = GraphCompiler(registry).compile(incomplete.snapshot())
    assert {
        issue.port_id for issue in result.report.errors if issue.code == "required_input_missing"
    } == {
        "midi_1",
        "midi_2",
    }

    different_clocks = GraphDocument()
    different_clocks.add_node("synmachine.input.load_camera", node_id=SOURCE_A)
    different_clocks.add_node("synmachine.input.load_camera", node_id=SOURCE_B)
    different_clocks.add_node(MIDI_MERGE_TYPE_ID, node_id=UTILITY)
    # IMAGE is intentionally incompatible too, but the graph must independently prove clock safety.
    different_clocks.add_connection(SOURCE_A, "image", UTILITY, "midi_1")
    different_clocks.add_connection(SOURCE_B, "image", UTILITY, "midi_2")
    compiled = GraphCompiler(registry).compile(different_clocks.snapshot())
    assert any(
        issue.code == "clock_mismatch" and issue.node_id == UTILITY
        for issue in compiled.report.errors
    )


def test_registry_contains_all_midi_value_utilities() -> None:
    registry_ids = {
        definition.type_id for definition in create_application_registry().definitions()
    }
    assert {MULTIPLY_VELOCITY_TYPE_ID, TRANSPOSE_TYPE_ID, MIDI_MERGE_TYPE_ID} <= registry_ids
    assert len(registry_ids) >= 54
