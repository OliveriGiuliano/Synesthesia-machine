"""Synthetic contour fixtures for deterministic Edges to Pitch semantics."""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest
from numpy.typing import NDArray

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    PortType,
    read_only_float32,
)
from synesthesia_machine.midi import resolve_musical_selector
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError
from synesthesia_machine.nodes.synesthesia import (
    AREA,
    CENTROID_X,
    CENTROID_Y,
    CIRCULARITY,
    EDGE_STRENGTH,
    EDGES_TO_PITCH_TYPE_ID,
    EXTERNAL,
    ORIENTATION,
    PERIMETER,
    TREE,
    EdgesToPitchRuntime,
    create_edges_to_pitch_definitions,
    edges_to_midi_state,
    extract_contour_features,
)
from synesthesia_machine.nodes.synesthesia.musical import CommonMusicalSettings
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000640")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000000641")
NODE = UUID("00000000-0000-0000-0000-000000000642")
SOURCE = UUID("00000000-0000-0000-0000-000000000643")


def _context(clock: UUID = CLOCK) -> FrameContext:
    return FrameContext(clock, 1, 0, 0.0, 1, None, False)


def _channel(values: NDArray[np.float32], *, clock: UUID = CLOCK) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(values, dtype=np.float32)),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        _context(clock),
    )


def _settings(minimum: int = 60, maximum: int = 64) -> CommonMusicalSettings:
    return CommonMusicalSettings(
        resolve_musical_selector("C", "chromatic", minimum, maximum),
        0,
        16,
        1,
        127,
    )


def _rectangles() -> ChannelFrame:
    values = np.zeros((20, 20), dtype=np.float32)
    values[2:8, 1:5] = 0.5
    values[10:17, 12:19] = 1.0
    return _channel(values)


def test_contour_features_are_spatially_ordered_and_have_fixed_units() -> None:
    features = extract_contour_features(
        _rectangles(),
        retrieval_mode=EXTERNAL,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
    )
    assert len(features) == 2
    left, right = features
    assert left.area == pytest.approx(15.0)
    assert left.perimeter == pytest.approx(16.0)
    assert left.centroid_x == pytest.approx(2.5 / 19.0)
    assert left.edge_strength == pytest.approx(0.5)
    assert right.area == pytest.approx(36.0)
    assert right.centroid_x == pytest.approx(15.0 / 19.0)
    assert right.orientation == pytest.approx(0.0)
    assert 0.0 < right.circularity <= 1.0


def test_retrieval_filter_and_work_cap_are_deterministic() -> None:
    ring = np.zeros((24, 24), dtype=np.float32)
    ring[2:20, 2:20] = 1.0
    ring[7:15, 7:15] = 0.0
    channel = _channel(ring)
    external = extract_contour_features(
        channel,
        retrieval_mode=EXTERNAL,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
    )
    tree = extract_contour_features(
        channel,
        retrieval_mode=TREE,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
    )
    capped = extract_contour_features(
        channel,
        retrieval_mode=TREE,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=1,
    )
    filtered = extract_contour_features(
        channel,
        retrieval_mode=TREE,
        minimum_contour_area=400.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
    )
    assert len(external) == 1
    assert len(tree) == 2
    assert capped == tree[:1]
    assert filtered == ()


def test_edges_map_explicit_pitch_velocity_ranges_and_common_rules() -> None:
    state = edges_to_midi_state(
        _rectangles(),
        retrieval_mode=EXTERNAL,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
        pitch_feature=CENTROID_X,
        velocity_feature=AREA,
        pitch_minimum=0.0,
        pitch_maximum=1.0,
        velocity_minimum=0.0,
        velocity_maximum=40.0,
        settings=CommonMusicalSettings(
            resolve_musical_selector("D", "major", 60, 72),
            4,
            1,
            10,
            110,
        ),
        node_id=NODE,
        context=_context(),
    )
    assert state.notes == {MidiNoteKey(4, 69): 100}


@pytest.mark.parametrize(
    "feature", [PERIMETER, AREA, CENTROID_X, CENTROID_Y, ORIENTATION, CIRCULARITY]
)
def test_all_contour_pitch_features_produce_finite_notes(feature: str) -> None:
    state = edges_to_midi_state(
        _rectangles(),
        retrieval_mode=EXTERNAL,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
        pitch_feature=feature,
        velocity_feature=EDGE_STRENGTH,
        pitch_minimum=0.0,
        pitch_maximum=180.0 if feature == ORIENTATION else 40.0,
        velocity_minimum=0.0,
        velocity_maximum=1.0,
        settings=_settings(),
        node_id=NODE,
        context=_context(),
    )
    assert isinstance(state, MidiStateFrame)
    assert state.notes


def test_runtime_errors_are_recoverable_and_definition_is_registered() -> None:
    definition = create_edges_to_pitch_definitions()[0]
    assert definition.type_id == EDGES_TO_PITCH_TYPE_ID
    assert definition.execution_kind is ExecutionKind.STATELESS
    assert tuple(port.id for port in definition.inputs) == ("edges",)
    registry = create_application_registry()
    assert len(registry.definitions()) >= 56
    assert registry.require(EDGES_TO_PITCH_TYPE_ID).type_id == EDGES_TO_PITCH_TYPE_ID

    parameters, errors = definition.parameter_values({})
    assert not errors
    runtime = EdgesToPitchRuntime(NODE)
    with pytest.raises(ExpectedNodeError, match="clock does not match") as captured:
        runtime.process(
            {"edges": _channel(np.ones((4, 4), dtype=np.float32), clock=OTHER_CLOCK)},
            parameters,
            _context(),
        )
    assert captured.value.code == "invalid_edges_to_pitch"


def test_scheduler_suppresses_edges_node_for_no_data() -> None:
    definition = create_edges_to_pitch_definitions()[0]
    parameters, errors = definition.parameter_values({})
    assert not errors
    source = PortKey(SOURCE, "edges")
    scheduler = Scheduler(
        ExecutionPlan(
            UUID("00000000-0000-0000-0000-000000000644"),
            1,
            (
                CompiledNode(
                    NODE,
                    definition,
                    parameters=parameters,
                    input_bindings={"edges": InputBinding(source)},
                    input_types={"edges": PortType.CHANNEL},
                    output_types={"midi": PortType.MIDI_STATE},
                    clock_id=CLOCK,
                    is_static=False,
                ),
            ),
            frozenset({NODE}),
        )
    )
    try:
        result = scheduler.execute_tick(_context(), source_values={source: NoData})
    finally:
        scheduler.close()
    assert result.errors == ()
    assert result.values[PortKey(NODE, "midi")] is NoData
    assert NODE not in result.invocation_counts


def test_nonfinite_input_is_sanitized_and_invalid_ranges_are_rejected() -> None:
    values = np.array([[np.nan, np.inf], [-np.inf, 1.0]], dtype=np.float32)
    features = extract_contour_features(
        _channel(values),
        retrieval_mode=EXTERNAL,
        minimum_contour_area=0.0,
        minimum_contour_perimeter=0.0,
        contour_limit=10,
    )
    assert isinstance(features, tuple)
    definition = create_edges_to_pitch_definitions()[0]
    _, errors = definition.parameter_values({"pitch_minimum": 1.0, "pitch_maximum": 1.0})
    assert any("greater than minimum" in error for error in errors)
