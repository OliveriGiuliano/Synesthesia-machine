"""Synthetic translation and safety tests for the Phase 6 Optical Flow node."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    read_only_float32,
)
from synesthesia_machine.midi import resolve_musical_selector
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, ResetReason
from synesthesia_machine.nodes.synesthesia import (
    ACCURATE,
    BALANCED,
    CELL_NOTES,
    DIRECTION,
    FAST,
    GLOBAL_HISTOGRAM,
    HORIZONTAL_POSITION,
    MAGNITUDE,
    MAXIMUM_MAGNITUDE,
    MEAN_MAGNITUDE,
    MOVING_PIXEL_FRACTION,
    OPTICAL_FLOW_TYPE_ID,
    VERTICAL_POSITION,
    CommonMusicalSettings,
    OpticalFlowRuntime,
    calculate_dense_flow,
    create_optical_flow_definitions,
    extract_flow_cells,
    flow_to_midi_state,
)
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000660")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000000661")
NODE = UUID("00000000-0000-0000-0000-000000000662")
CURRENT_SOURCE = UUID("00000000-0000-0000-0000-000000000663")
REFERENCE_SOURCE = UUID("00000000-0000-0000-0000-000000000664")
DOCUMENT = UUID("00000000-0000-0000-0000-000000000665")


def _context(clock: UUID = CLOCK, tick: int = 1, source_frame: int | None = 0) -> FrameContext:
    return FrameContext(
        clock, tick, source_frame, float(source_frame or 0) / 30.0, tick, None, False
    )


def _image(
    values: NDArray[np.float32],
    *,
    clock: UUID = CLOCK,
    tick: int = 1,
    source_frame: int | None = 0,
) -> ImageFrame:
    rgb = np.repeat(np.asarray(values, dtype=np.float32)[..., None], 3, axis=2)
    return ImageFrame(
        read_only_float32(rgb),
        ColorSpace.LINEAR_RGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        _context(clock, tick, source_frame),
        FrameProvenance(CURRENT_SOURCE, "synthetic"),
    )


def _translation(
    dx: int,
    dy: int,
    *,
    current_tick: int = 2,
    reference_tick: int = 1,
    current_source_frame: int = 1,
    reference_source_frame: int = 0,
) -> tuple[ImageFrame, ImageFrame]:
    random = np.random.default_rng(660).random((96, 96), dtype=np.float32)
    reference_values = cv2.GaussianBlur(random, (0, 0), 2.0)
    reference_values = (reference_values - reference_values.min()) / (
        reference_values.max() - reference_values.min()
    )
    transform = np.array(((1.0, 0.0, dx), (0.0, 1.0, dy)), dtype=np.float32)
    current_values = cv2.warpAffine(
        reference_values,
        transform,
        (96, 96),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
    return (
        _image(
            np.asarray(current_values, dtype=np.float32),
            tick=current_tick,
            source_frame=current_source_frame,
        ),
        _image(
            np.asarray(reference_values, dtype=np.float32),
            tick=reference_tick,
            source_frame=reference_source_frame,
        ),
    )


def _flow(values: NDArray[np.float32]) -> NDArray[np.float32]:
    return np.ascontiguousarray(values, dtype=np.float32)


def _settings(
    minimum: int = 60,
    maximum: int = 64,
    *,
    root: str = "C",
    scale: str = "chromatic",
    channel: int = 0,
    polyphony: int = 16,
    minimum_velocity: int = 1,
    maximum_velocity: int = 127,
) -> CommonMusicalSettings:
    return CommonMusicalSettings(
        resolve_musical_selector(root, scale, minimum, maximum),
        channel,
        polyphony,
        minimum_velocity,
        maximum_velocity,
    )


def _parameters(**overrides: object) -> Mapping[str, ParameterValue]:
    values, errors = create_optical_flow_definitions()[0].parameter_values(overrides)
    assert not errors
    return values


def _flow_state(
    flow: NDArray[np.float32],
    *,
    pitch_feature: str = DIRECTION,
    velocity_feature: str = MEAN_MAGNITUDE,
    grid_rows: int = 1,
    grid_columns: int = 1,
    aggregation: str = CELL_NOTES,
    magnitude_minimum: float = 0.0,
    magnitude_maximum: float = 4.0,
    settings: CommonMusicalSettings | None = None,
) -> MidiStateFrame:
    return flow_to_midi_state(
        flow,
        minimum_motion_magnitude=0.5,
        pitch_feature=pitch_feature,
        velocity_feature=velocity_feature,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        aggregation=aggregation,
        magnitude_minimum=magnitude_minimum,
        magnitude_maximum=magnitude_maximum,
        settings=settings or _settings(),
        node_id=NODE,
        context=_context(),
    )


@pytest.mark.parametrize("preset", [FAST, BALANCED, ACCURATE])
def test_presets_recover_known_horizontal_translation(preset: str) -> None:
    current, reference = _translation(4, 0)
    flow = calculate_dense_flow(current, reference, flow_preset=preset)
    core = flow[16:-16, 16:-16]
    mean = np.mean(core, axis=(0, 1), dtype=np.float64)
    assert mean == pytest.approx((4.0, 0.0), abs=0.05)
    assert flow.dtype == np.float32
    assert flow.flags.c_contiguous
    assert not flow.flags.writeable


@pytest.mark.parametrize(
    ("dx", "dy"),
    [(4, 0), (-4, 0), (0, 4), (0, -4), (3, 2)],
)
def test_balanced_preset_recovers_signed_synthetic_translations(dx: int, dy: int) -> None:
    current, reference = _translation(dx, dy)
    flow = calculate_dense_flow(current, reference, flow_preset=BALANCED)
    mean = np.mean(flow[16:-16, 16:-16], axis=(0, 1), dtype=np.float64)
    assert mean == pytest.approx((dx, dy), abs=0.05)


def test_grid_cells_are_row_major_thresholded_and_fixed_unit() -> None:
    flow = np.zeros((4, 4, 2), dtype=np.float32)
    flow[:2, :2, 0] = 1.0
    flow[:2, 2:, 1] = 2.0
    flow[2:, :2, 0] = -3.0
    flow[2:, 2:, 0] = 0.25
    cells = extract_flow_cells(
        _flow(flow), grid_rows=2, grid_columns=2, minimum_motion_magnitude=0.5
    )
    assert tuple((cell.row, cell.column) for cell in cells) == ((0, 0), (0, 1), (1, 0))
    assert (cells[0].center_x, cells[0].center_y) == pytest.approx((1 / 6, 1 / 6))
    assert (cells[1].center_x, cells[1].center_y) == pytest.approx((5 / 6, 1 / 6))
    assert (cells[2].mean_dx, cells[2].mean_dy, cells[2].mean_magnitude) == pytest.approx(
        (-3.0, 0.0, 3.0)
    )
    assert all(cell.moving_pixel_fraction == 1.0 for cell in cells)


def test_direction_mapping_and_common_musical_rules_are_deterministic() -> None:
    flow = np.zeros((4, 4, 2), dtype=np.float32)
    flow[:2, :2] = (4.0, 0.0)
    flow[:2, 2:] = (0.0, 4.0)
    flow[2:, :2] = (-4.0, 0.0)
    flow[2:, 2:] = (0.0, -4.0)
    settings = _settings(
        60,
        72,
        root="D",
        scale="major",
        channel=4,
        polyphony=2,
        minimum_velocity=10,
        maximum_velocity=110,
    )
    state = _flow_state(_flow(flow), grid_rows=2, grid_columns=2, settings=settings)
    assert settings.selector.allowed_notes == (61, 62, 64, 66, 67, 69, 71)
    assert state.notes == {MidiNoteKey(4, 61): 110, MidiNoteKey(4, 64): 110}


@pytest.mark.parametrize(
    ("feature", "rows", "columns", "expected"),
    [
        (HORIZONTAL_POSITION, 1, 2, (61, 63)),
        (VERTICAL_POSITION, 2, 1, (61, 63)),
        (MAGNITUDE, 1, 2, (61, 64)),
    ],
)
def test_position_and_magnitude_pitch_features(
    feature: str,
    rows: int,
    columns: int,
    expected: tuple[int, ...],
) -> None:
    flow = np.zeros((4, 4, 2), dtype=np.float32)
    if feature == MAGNITUDE:
        flow[:, :2, 0] = 1.0
        flow[:, 2:, 0] = 4.0
    else:
        flow[..., 0] = 4.0
    state = _flow_state(
        _flow(flow),
        pitch_feature=feature,
        velocity_feature=MOVING_PIXEL_FRACTION,
        grid_rows=rows,
        grid_columns=columns,
    )
    assert tuple(key.note for key in state.notes) == expected


@pytest.mark.parametrize(
    ("feature", "expected_velocity"),
    [(MEAN_MAGNITUDE, 51), (MAXIMUM_MAGNITUDE, 76), (MOVING_PIXEL_FRACTION, 51)],
)
def test_velocity_features_use_explicit_ranges(feature: str, expected_velocity: int) -> None:
    flow = np.zeros((2, 2, 2), dtype=np.float32)
    flow[0, 0, 0] = 1.0
    flow[0, 1, 0] = 3.0
    state = _flow_state(
        _flow(flow),
        velocity_feature=feature,
        settings=_settings(minimum_velocity=1, maximum_velocity=101),
    )
    assert state.notes == {MidiNoteKey(0, 60): expected_velocity}


def test_global_histogram_groups_vectors_by_selected_pitch_feature() -> None:
    flow = np.zeros((2, 4, 2), dtype=np.float32)
    flow[:, :2, 0] = 2.0
    flow[:, 2:, 0] = -2.0
    state = _flow_state(_flow(flow), aggregation=GLOBAL_HISTOGRAM)
    assert state.notes == {MidiNoteKey(0, 60): 64, MidiNoteKey(0, 62): 64}


def test_fixed_magnitude_normalization_is_not_frame_adaptive() -> None:
    slow = _flow_state(_flow(np.full((2, 2, 2), (1.0, 0.0), dtype=np.float32)))
    fast = _flow_state(_flow(np.full((2, 2, 2), (2.0, 0.0), dtype=np.float32)))
    assert slow.notes == {MidiNoteKey(0, 60): 33}
    assert fast.notes == {MidiNoteKey(0, 60): 64}


def test_runtime_allows_reference_tick_gaps_and_is_reset_independent() -> None:
    current, reference = _translation(
        4,
        0,
        current_tick=20,
        reference_tick=2,
        current_source_frame=80,
        reference_source_frame=5,
    )
    runtime = OpticalFlowRuntime(NODE)
    parameters = _parameters(
        grid_rows=1,
        grid_columns=1,
        midi_minimum=60,
        midi_maximum=64,
        magnitude_maximum=4.0,
    )
    expected = runtime.process(
        {"current": current, "reference": reference}, parameters, current.context
    )
    for reason in ResetReason:
        runtime.reset(reason)
        assert (
            runtime.process(
                {"current": current, "reference": reference}, parameters, current.context
            )
            == expected
        )
    runtime.close()


@pytest.mark.parametrize("failure", ["input_clock", "execution_clock", "shape", "nonfinite"])
def test_runtime_validation_failures_are_recoverable(failure: str) -> None:
    current, reference = _translation(4, 0)
    execution_context = current.context
    if failure == "input_clock":
        reference = _image(reference.data[..., 0], clock=OTHER_CLOCK)
    elif failure == "execution_clock":
        execution_context = _context(OTHER_CLOCK)
    elif failure == "shape":
        reference = _image(reference.data[:80, :80, 0])
    else:
        values = np.array(reference.data[..., 0], copy=True)
        values[10, 10] = np.nan
        reference = _image(values)
    runtime = OpticalFlowRuntime(NODE)
    with pytest.raises(ExpectedNodeError) as captured:
        runtime.process(
            {"current": current, "reference": reference},
            _parameters(),
            execution_context,
        )
    assert captured.value.code == "invalid_optical_flow"


def test_scheduler_no_data_suppresses_optical_flow_invocation() -> None:
    current_source = PortKey(CURRENT_SOURCE, "image")
    reference_source = PortKey(REFERENCE_SOURCE, "image")
    definition = create_optical_flow_definitions()[0]
    scheduler = Scheduler(
        ExecutionPlan(
            DOCUMENT,
            1,
            (
                CompiledNode(
                    NODE,
                    definition,
                    parameters=_parameters(),
                    input_bindings={
                        "current": InputBinding(current_source),
                        "reference": InputBinding(reference_source),
                    },
                    input_types={"current": PortType.IMAGE, "reference": PortType.IMAGE},
                    output_types={"midi": PortType.MIDI_STATE},
                    clock_id=CLOCK,
                    is_static=False,
                ),
            ),
            frozenset({NODE}),
        )
    )
    try:
        result = scheduler.execute_tick(
            _context(), source_values={current_source: NoData, reference_source: NoData}
        )
    finally:
        scheduler.close()
    assert result.errors == ()
    assert result.values[PortKey(NODE, "midi")] is NoData
    assert NODE not in result.invocation_counts


def test_definition_presets_registry_and_range_validation_contracts() -> None:
    definition = create_optical_flow_definitions()[0]
    assert definition.type_id == OPTICAL_FLOW_TYPE_ID
    assert definition.execution_kind is ExecutionKind.STATELESS
    assert tuple(port.id for port in definition.inputs) == ("current", "reference")
    assert definition.parameter("flow_preset").choices == (FAST, BALANCED, ACCURATE)  # type: ignore[union-attr]
    assert definition.parameter("grid_rows").default == 4  # type: ignore[union-attr]
    assert definition.parameter("grid_columns").default == 4  # type: ignore[union-attr]
    assert definition.parameter_groups[0].id == "musical"
    registry = create_application_registry()
    assert len(registry.definitions()) == 65
    assert registry.require(OPTICAL_FLOW_TYPE_ID).type_id == OPTICAL_FLOW_TYPE_ID
    for overrides, expected in (
        ({"minimum_motion_magnitude": float("nan")}, "finite"),
        ({"magnitude_minimum": 1.0, "magnitude_maximum": 1.0}, "magnitude maximum"),
    ):
        _, errors = definition.parameter_values(overrides)
        assert any(expected in error for error in errors)
