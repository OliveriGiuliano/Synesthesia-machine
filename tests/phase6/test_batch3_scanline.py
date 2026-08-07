"""Deterministic Scanline algorithm, state, reset, and registry tests."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.midi import resolve_musical_selector
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, ResetReason
from synesthesia_machine.nodes.synesthesia import (
    BOTTOM_TO_TOP,
    MAXIMUM,
    MEAN,
    PING_PONG,
    SCANLINE_TYPE_ID,
    TOP_TO_BOTTOM,
    ScanlineRuntime,
    create_scanline_definitions,
    scanline_to_midi_state,
)
from synesthesia_machine.nodes.synesthesia.musical import CommonMusicalSettings
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000630")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000000634")
SOURCE = UUID("00000000-0000-0000-0000-000000000631")
NODE = UUID("00000000-0000-0000-0000-000000000632")


def _context(tick: int = 1, clock: UUID = CLOCK) -> FrameContext:
    return FrameContext(clock, tick, tick - 1, 0.0, tick, None, False)


def _channel(rows: list[list[float]], *, tick: int = 1, clock: UUID = CLOCK) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(rows, dtype=np.float32)),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        _context(tick, clock),
    )


def _settings(minimum: int = 60, maximum: int = 62) -> CommonMusicalSettings:
    return CommonMusicalSettings(
        resolve_musical_selector("C", "chromatic", minimum, maximum),
        0,
        16,
        1,
        127,
    )


def _parameters(**overrides: object) -> Mapping[str, ParameterValue]:
    definition = create_scanline_definitions()[0]
    values, errors = definition.parameter_values(
        {"midi_minimum": 60, "midi_maximum": 62, **overrides}
    )
    assert not errors
    return values


def _notes(
    runtime: ScanlineRuntime, channel: ChannelFrame, **parameters: object
) -> Mapping[MidiNoteKey, int]:
    output = runtime.process({"value": channel}, _parameters(**parameters), channel.context)["midi"]
    assert isinstance(output, MidiStateFrame)
    return output.notes


def test_scanline_area_resize_threshold_curve_and_band_aggregation() -> None:
    channel = _channel([[0.0, 0.0, 1.0, 1.0], [0.5, 0.5, 0.5, 0.5], [0.0, 1.0, 0.0, 1.0]])
    output = scanline_to_midi_state(
        channel,
        row_index=0,
        line_thickness=1,
        aggregation=MEAN,
        activation_threshold=0.0,
        velocity_curve_exponent=1.0,
        settings=_settings(60, 61),
        node_id=NODE,
        context=channel.context,
    )
    assert output.notes == {MidiNoteKey(0, 61): 127}

    curved = scanline_to_midi_state(
        channel,
        row_index=1,
        line_thickness=1,
        aggregation=MEAN,
        activation_threshold=0.25,
        velocity_curve_exponent=2.0,
        settings=_settings(60, 60),
        node_id=NODE,
        context=channel.context,
    )
    assert curved.notes == {MidiNoteKey(0, 60): 15}

    maximum = scanline_to_midi_state(
        channel,
        row_index=1,
        line_thickness=3,
        aggregation=MAXIMUM,
        activation_threshold=0.0,
        velocity_curve_exponent=1.0,
        settings=_settings(60, 63),
        node_id=NODE,
        context=channel.context,
    )
    assert maximum.notes == {
        MidiNoteKey(0, 60): 64,
        MidiNoteKey(0, 61): 127,
        MidiNoteKey(0, 62): 127,
        MidiNoteKey(0, 63): 127,
    }


def test_scanline_applies_common_scale_range_channel_polyphony_and_velocity() -> None:
    runtime = ScanlineRuntime(NODE)
    channel = _channel([[0.25, 1.0, 1.0, 0.5, 0.9, 0.8, 0.7]])
    notes = _notes(
        runtime,
        channel,
        direction=TOP_TO_BOTTOM,
        root_pitch_class="D",
        scale="major",
        midi_minimum=60,
        midi_maximum=72,
        midi_channel=5,
        maximum_polyphony=2,
        minimum_velocity=10,
        maximum_velocity=110,
    )
    assert notes == {
        MidiNoteKey(4, 62): 110,
        MidiNoteKey(4, 64): 110,
    }


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (BOTTOM_TO_TOP, (62, 61, 60, 62)),
        (TOP_TO_BOTTOM, (60, 61, 62, 60)),
        (PING_PONG, (60, 61, 62, 61, 60)),
    ],
)
def test_scanline_direction_sequences_are_deterministic(
    direction: str,
    expected: tuple[int, ...],
) -> None:
    runtime = ScanlineRuntime(NODE)
    channel = _channel([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    sequence = tuple(
        next(iter(_notes(runtime, channel, direction=direction))).note for _ in expected
    )
    assert sequence == expected


def test_scanline_advance_direction_and_height_changes_restart_position() -> None:
    runtime = ScanlineRuntime(NODE)
    channel = _channel([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM, advance_rows=2))).note == 60
    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM, advance_rows=2))).note == 62
    assert next(iter(_notes(runtime, channel, direction=BOTTOM_TO_TOP))).note == 62
    shorter = _channel([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    assert next(iter(_notes(runtime, shorter, direction=BOTTOM_TO_TOP))).note == 62


@pytest.mark.parametrize("reason", list(ResetReason))
def test_every_lifecycle_reset_reason_restarts_scanline(reason: ResetReason) -> None:
    runtime = ScanlineRuntime(NODE)
    channel = _channel([[1.0, 0.0], [0.0, 1.0]])
    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM))).note == 60
    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM))).note == 61
    runtime.reset(reason)
    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM))).note == 60


def test_scheduler_nodata_and_tick_gaps_do_not_advance_scanline() -> None:
    source = PortKey(SOURCE, "value")
    scheduler = Scheduler(_execution_plan(source, direction=TOP_TO_BOTTOM))
    channel = _channel([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    try:
        first = scheduler.execute_tick(_context(1), source_values={source: channel})
        dropped = scheduler.execute_tick(_context(50), source_values={source: NoData})
        second = scheduler.execute_tick(
            _context(100), source_values={source: _channel(channel.data.tolist(), tick=100)}
        )
    finally:
        scheduler.close()
    assert next(iter(_runtime_notes(first.values[PortKey(NODE, "midi")]))).note == 60
    assert dropped.values[PortKey(NODE, "midi")] is NoData
    assert NODE not in dropped.invocation_counts
    assert next(iter(_runtime_notes(second.values[PortKey(NODE, "midi")]))).note == 61


def test_scheduler_clock_error_is_recoverable_and_does_not_advance_scanline() -> None:
    source = PortKey(SOURCE, "value")
    scheduler = Scheduler(_execution_plan(source, direction=TOP_TO_BOTTOM))
    rows = [[1.0, 0.0], [0.0, 1.0]]
    try:
        first = scheduler.execute_tick(
            _context(1),
            source_values={source: _channel(rows)},
        )
        failed = scheduler.execute_tick(
            _context(2),
            source_values={source: _channel(rows, tick=2, clock=OTHER_CLOCK)},
        )
        recovered = scheduler.execute_tick(
            _context(3),
            source_values={source: _channel(rows, tick=3)},
        )
    finally:
        scheduler.close()

    assert next(iter(_runtime_notes(first.values[PortKey(NODE, "midi")]))).note == 60
    assert failed.values[PortKey(NODE, "midi")] is NoData
    assert failed.invocation_counts == {NODE: 1}
    assert len(failed.errors) == 1
    assert failed.errors[0].code == "invalid_scanline"
    assert failed.errors[0].recoverable
    assert next(iter(_runtime_notes(recovered.values[PortKey(NODE, "midi")]))).note == 61


def test_scanline_metadata_registry_clock_and_nonfinite_contracts() -> None:
    definition = create_scanline_definitions()[0]
    assert definition.type_id == SCANLINE_TYPE_ID
    assert definition.execution_kind is ExecutionKind.STATEFUL
    assert tuple(port.id for port in definition.inputs) == ("value",)
    assert tuple(port.id for port in definition.outputs) == ("midi",)
    assert definition.parameter_groups[0].id == "musical"
    parameters, errors = definition.parameter_values({})
    assert not errors
    assert parameters["direction"] == BOTTOM_TO_TOP
    assert parameters["advance_rows"] == 1
    assert parameters["line_thickness"] == 1
    assert parameters["aggregation"] == MEAN
    registry = create_application_registry()
    assert len(registry.definitions()) == 55
    assert registry.require(SCANLINE_TYPE_ID).type_id == definition.type_id

    channel = _channel([[np.nan, np.inf, -np.inf]])
    output = scanline_to_midi_state(
        channel,
        row_index=0,
        line_thickness=1,
        aggregation=MEAN,
        activation_threshold=0.0,
        velocity_curve_exponent=1.0,
        settings=_settings(),
        node_id=NODE,
        context=channel.context,
    )
    assert output.notes == {MidiNoteKey(0, 61): 127}
    with pytest.raises(ValueError, match="clock does not match"):
        scanline_to_midi_state(
            channel,
            row_index=0,
            line_thickness=1,
            aggregation=MEAN,
            activation_threshold=0.0,
            velocity_curve_exponent=1.0,
            settings=_settings(),
            node_id=NODE,
            context=_context(clock=UUID(int=999)),
        )


def test_scanline_runtime_rejects_invalid_direction_and_advance_without_advancing() -> None:
    runtime = ScanlineRuntime(NODE)
    channel = _channel([[1.0, 0.0], [0.0, 1.0]])
    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM))).note == 60

    for parameter_id, invalid_value, message in (
        ("direction", "SIDEWAYS", "Unknown scanline direction"),
        ("advance_rows", 0, "advance rows must be at least one"),
    ):
        parameters = dict(_parameters())
        parameters[parameter_id] = invalid_value
        with pytest.raises(ExpectedNodeError, match=message):
            runtime.process({"value": channel}, parameters, channel.context)

    assert next(iter(_notes(runtime, channel, direction=TOP_TO_BOTTOM))).note == 61


def _runtime_notes(value: RuntimeValue) -> Mapping[MidiNoteKey, int]:
    assert isinstance(value, MidiStateFrame)
    return value.notes


def _execution_plan(source: PortKey, **parameters: object) -> ExecutionPlan:
    return ExecutionPlan(
        UUID("00000000-0000-0000-0000-000000000635"),
        1,
        (
            CompiledNode(
                NODE,
                create_scanline_definitions()[0],
                parameters=_parameters(**parameters),
                input_bindings={"value": InputBinding(source)},
                input_types={"value": PortType.CHANNEL},
                output_types={"midi": PortType.MIDI_STATE},
                clock_id=CLOCK,
                is_static=False,
            ),
        ),
        frozenset({NODE}),
    )
