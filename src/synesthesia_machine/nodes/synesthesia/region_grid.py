"""Spatial image-region measurements mapped directly to desired MIDI notes."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ColorSpace,
    FrameContext,
    ImageFrame,
    MidiStateFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import convert_image, image_to_luminance
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    ResetReason,
)
from synesthesia_machine.nodes.synesthesia.musical import (
    COMMON_MUSICAL_PARAMETER_GROUP,
    CommonMusicalSettings,
    common_musical_parameter_specs,
    midi_state_from_candidates,
    resolve_common_musical_settings,
    validate_common_musical_parameters,
)

REGION_GRID_TYPE_ID = "synmachine.synesthesia.region_grid"
BRIGHTNESS = "BRIGHTNESS"
CONTRAST = "CONTRAST"
SATURATION = "SATURATION"
VALUE = "VALUE"
RED = "RED"
GREEN = "GREEN"
BLUE = "BLUE"
REGION_METRICS = (BRIGHTNESS, CONTRAST, SATURATION, VALUE, RED, GREEN, BLUE)


@dataclass(frozen=True, slots=True)
class RegionMeasurement:
    """Normalized measurement for one stable row-major image-grid cell."""

    row: int
    column: int
    value: float


class RegionGridRuntime:
    """Stateless image-grid measurement and desired-MIDI-state projection."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            midi = region_grid_to_midi_state(
                _image(inputs["image"]),
                metric=_text(parameters["metric"]),
                grid_rows=_integer(parameters["grid_rows"]),
                grid_columns=_integer(parameters["grid_columns"]),
                activation_threshold=_number(parameters["activation_threshold"]),
                settings=resolve_common_musical_settings(parameters),
                node_id=self.node_id,
                context=context,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_region_grid", str(error)) from error
        return {"midi": midi}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def region_grid_to_midi_state(
    image: ImageFrame,
    *,
    metric: str,
    grid_rows: int,
    grid_columns: int,
    activation_threshold: float,
    settings: CommonMusicalSettings,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Measure cells and map top-left through bottom-right across allowed notes."""

    if image.context.clock_id != context.clock_id:
        raise ValueError("Region Grid image clock does not match the execution clock")
    _validate_algorithm_parameters(
        metric=metric,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        activation_threshold=activation_threshold,
    )
    measurements = measure_image_regions(
        image,
        metric=metric,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
    )
    allowed = settings.selector.allowed_notes
    cell_count = len(measurements)
    candidates: list[tuple[int, float]] = []
    for index, measurement in enumerate(measurements):
        if measurement.value <= activation_threshold:
            continue
        strength = (measurement.value - activation_threshold) / (1.0 - activation_threshold)
        candidates.append((allowed[_note_index(index, cell_count, len(allowed))], strength))
    return midi_state_from_candidates(
        candidates,
        settings=settings,
        context=context,
        source_node_id=node_id,
    )


def measure_image_regions(
    image: ImageFrame,
    *,
    metric: str,
    grid_rows: int,
    grid_columns: int,
) -> tuple[RegionMeasurement, ...]:
    """Return normalized cell measurements in top-to-bottom, left-to-right order."""

    height, width = image.data.shape[:2]
    if height < 1 or width < 1:
        raise ValueError("Region Grid image must be non-empty")
    if grid_rows < 1 or grid_columns < 1:
        raise ValueError("Region Grid dimensions must be at least one")
    if grid_rows > height or grid_columns > width:
        raise ValueError("Region Grid dimensions cannot exceed image dimensions")
    if metric not in REGION_METRICS:
        raise ValueError(f"Unknown Region Grid metric: {metric!r}")

    luminance = image_to_luminance(image).data
    values, use_rms_contrast = _metric_values(image, metric, luminance)
    y_bounds = np.linspace(0, height, grid_rows + 1, dtype=np.int64)
    x_bounds = np.linspace(0, width, grid_columns + 1, dtype=np.int64)
    result: list[RegionMeasurement] = []
    for row in range(grid_rows):
        y0, y1 = int(y_bounds[row]), int(y_bounds[row + 1])
        for column in range(grid_columns):
            x0, x1 = int(x_bounds[column]), int(x_bounds[column + 1])
            cell = values[y0:y1, x0:x1]
            if use_rms_contrast:
                # In normalized imagery the maximum black/white RMS contrast is 0.5.
                measured = min(1.0, 2.0 * float(np.std(cell, dtype=np.float64)))
            else:
                measured = float(np.mean(cell, dtype=np.float64))
            result.append(RegionMeasurement(row, column, min(1.0, max(0.0, measured))))
    return tuple(result)


def create_region_grid_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            REGION_GRID_TYPE_ID,
            1,
            "Region Grid to Notes",
            "Synesthesia",
            (
                "Splits the picture into a grid of cells and lets each cell play its own note. "
                "You choose what to measure in each cell (brightness, contrast, colour strength, "
                "...). A cell plays only when the measurement passes your threshold, and stronger "
                "measurements play louder."
            ),
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
            (
                *common_musical_parameter_specs(),
                ParameterSpec(
                    "metric",
                    "Region metric",
                    PortType.STRING,
                    BRIGHTNESS,
                    help_text=(
                        "Selects what is measured inside every grid cell. Brightness uses "
                        "linear-light luminance; Contrast uses normalized RMS variation; "
                        "Saturation and Value use HSV; Red, Green, and Blue use sRGB channels."
                    ),
                    choices=REGION_METRICS,
                ),
                ParameterSpec(
                    "grid_rows",
                    "Grid rows",
                    PortType.INT,
                    4,
                    help_text=(
                        "Sets how many horizontal bands divide the image; each band becomes one "
                        "row of cells."
                    ),
                    minimum=1,
                    maximum=64,
                ),
                ParameterSpec(
                    "grid_columns",
                    "Grid columns",
                    PortType.INT,
                    4,
                    help_text=(
                        "Sets how many vertical bands divide the image; each band becomes one "
                        "column of cells."
                    ),
                    minimum=1,
                    maximum=64,
                ),
                ParameterSpec(
                    "activation_threshold",
                    "Activation threshold",
                    PortType.FLOAT,
                    0.5,
                    help_text=(
                        "Cells at or below this normalized measurement stay silent. Above it, "
                        "velocity rises from the minimum to the maximum velocity as the measured "
                        "value approaches 1."
                    ),
                    minimum=0.0,
                    maximum=1.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
            ),
            ExecutionKind.STATELESS,
            RegionGridRuntime,
            aliases=("image grid notes", "spatial regions", "grid threshold sequencer"),
            parameter_validator=_validate_parameters,
            parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
        ),
    )


def _metric_values(
    image: ImageFrame,
    metric: str,
    luminance: NDArray[np.float32],
) -> tuple[NDArray[np.float32], bool]:
    if metric == BRIGHTNESS:
        return np.asarray(np.clip(luminance, 0.0, 1.0), dtype=np.float32), False
    if metric == CONTRAST:
        return np.asarray(np.clip(luminance, 0.0, 1.0), dtype=np.float32), True
    if metric in (SATURATION, VALUE):
        hsv = convert_image(image, ColorSpace.HSV).data
        index = 1 if metric == SATURATION else 2
        return np.asarray(np.clip(hsv[..., index], 0.0, 1.0), dtype=np.float32), False
    rgb = convert_image(image, ColorSpace.SRGB).data
    index = {RED: 0, GREEN: 1, BLUE: 2}[metric]
    return np.asarray(np.clip(rgb[..., index], 0.0, 1.0), dtype=np.float32), False


def _note_index(cell_index: int, cell_count: int, note_count: int) -> int:
    if cell_count == 1 or note_count == 1:
        return 0
    return math.floor(cell_index * (note_count - 1) / (cell_count - 1) + 0.5)


def _validate_algorithm_parameters(
    *, metric: str, grid_rows: int, grid_columns: int, activation_threshold: float
) -> None:
    if metric not in REGION_METRICS:
        raise ValueError(f"Unknown Region Grid metric: {metric!r}")
    if grid_rows < 1 or grid_columns < 1:
        raise ValueError("Region Grid dimensions must be at least one")
    if not math.isfinite(activation_threshold) or not 0.0 <= activation_threshold <= 1.0:
        raise ValueError("Region Grid activation threshold must be finite and in the range 0..1")


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    try:
        _validate_algorithm_parameters(
            metric=_text(parameters["metric"]),
            grid_rows=_integer(parameters["grid_rows"]),
            grid_columns=_integer(parameters["grid_columns"]),
            activation_threshold=_number(parameters["activation_threshold"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
    return errors


def _image(value: object) -> ImageFrame:
    if isinstance(value, ImageFrame):
        return value
    raise TypeError(f"Expected ImageFrame, got {type(value).__name__}")


def _integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected integer value, got {type(value).__name__}")


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected numeric value, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string value, got {type(value).__name__}")


__all__ = [
    "BLUE",
    "BRIGHTNESS",
    "CONTRAST",
    "GREEN",
    "RED",
    "REGION_GRID_TYPE_ID",
    "REGION_METRICS",
    "SATURATION",
    "VALUE",
    "RegionGridRuntime",
    "RegionMeasurement",
    "create_region_grid_definitions",
    "measure_image_regions",
    "region_grid_to_midi_state",
]
