"""Spatial metric, note mapping, runtime, and metadata tests for Region Grid."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    MidiNoteKey,
    MidiStateFrame,
    ParameterValue,
    PortType,
    read_only_float32,
)
from synesthesia_machine.midi import resolve_musical_selector
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, ParameterEditorHint
from synesthesia_machine.nodes.synesthesia import (
    BLUE,
    BRIGHTNESS,
    CONTRAST,
    GREEN,
    RED,
    REGION_GRID_TYPE_ID,
    REGION_METRICS,
    SATURATION,
    VALUE,
    CommonMusicalSettings,
    RegionGridRuntime,
    create_region_grid_definitions,
    measure_image_regions,
    region_grid_to_midi_state,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000680")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000000681")
SOURCE = UUID("00000000-0000-0000-0000-000000000682")
NODE = UUID("00000000-0000-0000-0000-000000000683")


def _context(clock: UUID = CLOCK) -> FrameContext:
    return FrameContext(clock, 1, 0, 0.0, 1, None, False)


def _image(
    values: np.ndarray,
    *,
    color_space: ColorSpace = ColorSpace.LINEAR_RGB,
    clock: UUID = CLOCK,
) -> ImageFrame:
    data = np.asarray(values, dtype=np.float32)
    if data.ndim == 2:
        data = np.repeat(data[..., None], 3, axis=2)
    return ImageFrame(
        read_only_float32(data),
        color_space,
        ("R", "G", "B"),
        AlphaMode.NONE,
        _context(clock),
        FrameProvenance(SOURCE, "synthetic"),
    )


def _settings(
    minimum: int = 60,
    maximum: int = 63,
    *,
    channel: int = 0,
    minimum_velocity: int = 1,
    maximum_velocity: int = 101,
) -> CommonMusicalSettings:
    return CommonMusicalSettings(
        resolve_musical_selector("C", "chromatic", minimum, maximum),
        channel,
        16,
        minimum_velocity,
        maximum_velocity,
    )


def _parameters(**overrides: object) -> Mapping[str, ParameterValue]:
    values, errors = create_region_grid_definitions()[0].parameter_values(
        {"midi_minimum": 60, "midi_maximum": 63, **overrides}
    )
    assert not errors
    return values


def test_region_brightness_measurements_follow_stable_row_major_cells() -> None:
    image = _image(np.array(((0.2, 0.4), (0.6, 0.8)), dtype=np.float32))
    measurements = measure_image_regions(image, metric=BRIGHTNESS, grid_rows=2, grid_columns=2)

    assert [(item.row, item.column) for item in measurements] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [item.value for item in measurements] == pytest.approx((0.2, 0.4, 0.6, 0.8))


def test_region_metrics_cover_contrast_hsv_and_rgb_channels() -> None:
    contrast_image = _image(np.array(((0.5, 0.5, 0.0, 1.0),), dtype=np.float32))
    contrast = measure_image_regions(contrast_image, metric=CONTRAST, grid_rows=1, grid_columns=2)
    assert [item.value for item in contrast] == pytest.approx((0.0, 1.0))

    colors = _image(
        np.array([[[1.0, 0.0, 0.0], [0.5, 0.5, 0.5]]], dtype=np.float32),
        color_space=ColorSpace.SRGB,
    )
    saturation = measure_image_regions(colors, metric=SATURATION, grid_rows=1, grid_columns=2)
    value = measure_image_regions(colors, metric=VALUE, grid_rows=1, grid_columns=2)
    assert [item.value for item in saturation] == pytest.approx((1.0, 0.0))
    assert [item.value for item in value] == pytest.approx((1.0, 0.5))

    channels = _image(np.array([[[0.2, 0.4, 0.6]]], dtype=np.float32), color_space=ColorSpace.SRGB)
    channel_values = tuple(
        measure_image_regions(channels, metric=metric, grid_rows=1, grid_columns=1)[0].value
        for metric in (RED, GREEN, BLUE)
    )
    assert channel_values == pytest.approx((0.2, 0.4, 0.6))


def test_threshold_distance_controls_velocity_and_cells_span_allowed_notes() -> None:
    image = _image(np.array(((0.25, 0.5, 0.75, 1.0),), dtype=np.float32))
    output = region_grid_to_midi_state(
        image,
        metric=BRIGHTNESS,
        grid_rows=1,
        grid_columns=4,
        activation_threshold=0.5,
        settings=_settings(),
        node_id=NODE,
        context=image.context,
    )

    assert output.notes == {MidiNoteKey(0, 62): 51, MidiNoteKey(0, 63): 101}
    silent = region_grid_to_midi_state(
        image,
        metric=BRIGHTNESS,
        grid_rows=1,
        grid_columns=4,
        activation_threshold=1.0,
        settings=_settings(),
        node_id=NODE,
        context=image.context,
    )
    assert silent.notes == {}


def test_runtime_uses_common_musical_controls_and_wraps_expected_errors() -> None:
    image = _image(np.array(((0.0, 1.0),), dtype=np.float32))
    output = RegionGridRuntime(NODE).process(
        {"image": image},
        _parameters(
            grid_rows=1,
            grid_columns=2,
            activation_threshold=0.25,
            midi_channel=3,
        ),
        image.context,
    )["midi"]
    assert isinstance(output, MidiStateFrame)
    assert output.notes == {MidiNoteKey(2, 63): 127}

    with pytest.raises(ExpectedNodeError) as captured:
        RegionGridRuntime(NODE).process(
            {"image": image},
            _parameters(grid_rows=1, grid_columns=2),
            _context(OTHER_CLOCK),
        )
    assert captured.value.code == "invalid_region_grid"
    assert "clock" in str(captured.value).casefold()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"metric": "UNKNOWN", "grid_rows": 1, "grid_columns": 1}, "metric"),
        ({"metric": BRIGHTNESS, "grid_rows": 0, "grid_columns": 1}, "at least one"),
        ({"metric": BRIGHTNESS, "grid_rows": 2, "grid_columns": 1}, "image dimensions"),
    ],
)
def test_invalid_region_measurement_requests_are_rejected(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        measure_image_regions(_image(np.ones((1, 1), dtype=np.float32)), **kwargs)  # type: ignore[arg-type]


def test_definition_and_registry_expose_region_grid_as_stateless_synesthesia() -> None:
    definition = create_region_grid_definitions()[0]
    assert definition.type_id == REGION_GRID_TYPE_ID
    assert definition.execution_kind is ExecutionKind.STATELESS
    assert definition.category == "Synesthesia"
    assert tuple(port.id for port in definition.inputs) == ("image",)
    assert tuple(port.value_type for port in definition.outputs) == (PortType.MIDI_STATE,)
    assert definition.parameter("metric").choices == REGION_METRICS  # type: ignore[union-attr]
    assert definition.parameter("activation_threshold").editor_hint is ParameterEditorHint.SLIDER  # type: ignore[union-attr]
    assert definition.parameter_groups[0].id == "musical"
    assert len(definition.description) >= 250

    registry = create_application_registry()
    assert len(registry.definitions()) == 65
    assert registry.require(REGION_GRID_TYPE_ID).type_id == REGION_GRID_TYPE_ID


def test_parameter_validation_reports_non_finite_threshold() -> None:
    _, errors = create_region_grid_definitions()[0].parameter_values(
        {"activation_threshold": float("nan")}
    )
    assert any("finite" in error for error in errors)
