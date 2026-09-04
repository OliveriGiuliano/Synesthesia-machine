"""Deterministic dense-motion mapping from two images to desired MIDI state."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast
from uuid import UUID

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    FrameContext,
    ImageFrame,
    MidiStateFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import image_to_luminance
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
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

OPTICAL_FLOW_TYPE_ID = "synmachine.synesthesia.optical_flow"
FAST = "FAST"
BALANCED = "BALANCED"
ACCURATE = "ACCURATE"
DIRECTION = "DIRECTION"
HORIZONTAL_POSITION = "HORIZONTAL_POSITION"
VERTICAL_POSITION = "VERTICAL_POSITION"
MAGNITUDE = "MAGNITUDE"
MEAN_MAGNITUDE = "MEAN_MAGNITUDE"
MAXIMUM_MAGNITUDE = "MAXIMUM_MAGNITUDE"
MOVING_PIXEL_FRACTION = "MOVING_PIXEL_FRACTION"
CELL_NOTES = "CELL_NOTES"
GLOBAL_HISTOGRAM = "GLOBAL_HISTOGRAM"

FLOW_PRESETS = (FAST, BALANCED, ACCURATE)
PITCH_FEATURES = (DIRECTION, HORIZONTAL_POSITION, VERTICAL_POSITION, MAGNITUDE)
VELOCITY_FEATURES = (MEAN_MAGNITUDE, MAXIMUM_MAGNITUDE, MOVING_PIXEL_FRACTION)
AGGREGATIONS = (CELL_NOTES, GLOBAL_HISTOGRAM)


@dataclass(frozen=True, slots=True)
class FarnebackPreset:
    """Stable OpenCV parameters behind a persisted user-facing preset ID."""

    pyramid_scale: float
    levels: int
    window_size: int
    iterations: int
    polynomial_neighbourhood: int
    polynomial_sigma: float
    flags: int = 0


FARNEBACK_PRESETS: Mapping[str, FarnebackPreset] = MappingProxyType(
    {
        FAST: FarnebackPreset(0.5, 2, 13, 2, 5, 1.1),
        BALANCED: FarnebackPreset(0.5, 3, 21, 3, 5, 1.2),
        ACCURATE: FarnebackPreset(0.5, 5, 31, 5, 7, 1.5, cv2.OPTFLOW_FARNEBACK_GAUSSIAN),
    }
)


@dataclass(frozen=True, slots=True)
class FlowCell:
    """Finite active-motion measurements for one deterministic row-major grid cell."""

    row: int
    column: int
    center_x: float
    center_y: float
    mean_dx: float
    mean_dy: float
    mean_magnitude: float
    maximum_magnitude: float
    moving_pixel_fraction: float

    def pitch_value(
        self, feature: str, magnitude_minimum: float, magnitude_maximum: float
    ) -> float:
        if feature == DIRECTION:
            return _direction(self.mean_dx, self.mean_dy)
        if feature == HORIZONTAL_POSITION:
            return self.center_x
        if feature == VERTICAL_POSITION:
            return self.center_y
        if feature == MAGNITUDE:
            return _normalize(self.mean_magnitude, magnitude_minimum, magnitude_maximum)
        raise ValueError(f"Unknown optical-flow pitch feature: {feature!r}")

    def velocity_strength(
        self,
        feature: str,
        magnitude_minimum: float,
        magnitude_maximum: float,
    ) -> float:
        if feature == MEAN_MAGNITUDE:
            return _normalize(self.mean_magnitude, magnitude_minimum, magnitude_maximum)
        if feature == MAXIMUM_MAGNITUDE:
            return _normalize(self.maximum_magnitude, magnitude_minimum, magnitude_maximum)
        if feature == MOVING_PIXEL_FRACTION:
            return self.moving_pixel_fraction
        raise ValueError(f"Unknown optical-flow velocity feature: {feature!r}")


class OpticalFlowRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            midi = optical_flow_to_midi_state(
                _image(inputs["current"]),
                _image(inputs["reference"]),
                flow_preset=_text(parameters["flow_preset"]),
                minimum_motion_magnitude=_number(parameters["minimum_motion_magnitude"]),
                pitch_feature=_text(parameters["pitch_feature"]),
                velocity_feature=_text(parameters["velocity_feature"]),
                grid_rows=_integer(parameters["grid_rows"]),
                grid_columns=_integer(parameters["grid_columns"]),
                aggregation=_text(parameters["aggregation"]),
                magnitude_minimum=_number(parameters["magnitude_minimum"]),
                magnitude_maximum=_number(parameters["magnitude_maximum"]),
                settings=resolve_common_musical_settings(parameters),
                node_id=self.node_id,
                context=context,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_optical_flow", str(error)) from error
        return {"midi": midi}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def optical_flow_to_midi_state(
    current: ImageFrame,
    reference: ImageFrame,
    *,
    flow_preset: str,
    minimum_motion_magnitude: float,
    pitch_feature: str,
    velocity_feature: str,
    grid_rows: int,
    grid_columns: int,
    aggregation: str,
    magnitude_minimum: float,
    magnitude_maximum: float,
    settings: CommonMusicalSettings,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Calculate reference-to-current motion and map it through fixed musical ranges."""

    if current.context.clock_id != reference.context.clock_id:
        raise ValueError("Optical Flow inputs must use the same clock")
    if current.context.clock_id != context.clock_id:
        raise ValueError("Optical Flow image clock does not match the execution clock")
    flow = calculate_dense_flow(current, reference, flow_preset=flow_preset)
    return flow_to_midi_state(
        flow,
        minimum_motion_magnitude=minimum_motion_magnitude,
        pitch_feature=pitch_feature,
        velocity_feature=velocity_feature,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        aggregation=aggregation,
        magnitude_minimum=magnitude_minimum,
        magnitude_maximum=magnitude_maximum,
        settings=settings,
        node_id=node_id,
        context=context,
    )


def calculate_dense_flow(
    current: ImageFrame,
    reference: ImageFrame,
    *,
    flow_preset: str,
) -> NDArray[np.float32]:
    """Return a read-only `(height, width, dx/dy)` Farnebäck flow field."""

    if current.data.shape[:2] != reference.data.shape[:2]:
        raise ValueError("Optical Flow inputs must have equal dimensions")
    if current.context.clock_id != reference.context.clock_id:
        raise ValueError("Optical Flow inputs must use the same clock")
    try:
        preset = FARNEBACK_PRESETS[flow_preset]
    except KeyError as error:
        raise ValueError(f"Unknown optical-flow preset: {flow_preset!r}") from error
    current_luminance = _luminance_uint8(current)
    reference_luminance = _luminance_uint8(reference)
    output_buffer = np.empty((*current.data.shape[:2], 2), dtype=np.float32)
    try:
        raw = cast(
            NDArray[np.float32],
            cv2.calcOpticalFlowFarneback(
                reference_luminance,
                current_luminance,
                output_buffer,
                preset.pyramid_scale,
                preset.levels,
                preset.window_size,
                preset.iterations,
                preset.polynomial_neighbourhood,
                preset.polynomial_sigma,
                preset.flags,
            ),
        )
    except cv2.error as error:
        raise ValueError(f"Dense optical-flow calculation failed: {error}") from error
    flow = np.ascontiguousarray(raw, dtype=np.float32)
    if flow.shape != (*current.data.shape[:2], 2) or not np.isfinite(flow).all():
        raise ValueError("Dense optical-flow calculation produced an invalid vector field")
    flow.setflags(write=False)
    return flow


def flow_to_midi_state(
    flow: NDArray[np.float32],
    *,
    minimum_motion_magnitude: float,
    pitch_feature: str,
    velocity_feature: str,
    grid_rows: int,
    grid_columns: int,
    aggregation: str,
    magnitude_minimum: float,
    magnitude_maximum: float,
    settings: CommonMusicalSettings,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Map an already-calculated finite flow field to deterministic desired MIDI state."""

    _validate_algorithm_parameters(
        minimum_motion_magnitude=minimum_motion_magnitude,
        pitch_feature=pitch_feature,
        velocity_feature=velocity_feature,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        aggregation=aggregation,
        magnitude_minimum=magnitude_minimum,
        magnitude_maximum=magnitude_maximum,
    )
    _validate_flow(flow)
    height, width = flow.shape[:2]
    if grid_rows > height or grid_columns > width:
        raise ValueError("Optical Flow grid dimensions cannot exceed image dimensions")
    allowed = settings.selector.allowed_notes
    candidates = (
        _cell_candidates(
            flow,
            minimum_motion_magnitude,
            pitch_feature,
            velocity_feature,
            grid_rows,
            grid_columns,
            magnitude_minimum,
            magnitude_maximum,
            allowed,
        )
        if aggregation == CELL_NOTES
        else _histogram_candidates(
            flow,
            minimum_motion_magnitude,
            pitch_feature,
            velocity_feature,
            magnitude_minimum,
            magnitude_maximum,
            allowed,
        )
    )
    return midi_state_from_candidates(
        candidates,
        settings=settings,
        context=context,
        source_node_id=node_id,
    )


def extract_flow_cells(
    flow: NDArray[np.float32],
    *,
    grid_rows: int,
    grid_columns: int,
    minimum_motion_magnitude: float,
) -> tuple[FlowCell, ...]:
    """Return active cells in stable row-major order, omitting cells without accepted vectors."""

    _validate_flow(flow)
    _require_non_negative(minimum_motion_magnitude, "Minimum motion magnitude")
    height, width = flow.shape[:2]
    if grid_rows < 1 or grid_columns < 1:
        raise ValueError("Optical Flow grid dimensions must be at least one")
    if grid_rows > height or grid_columns > width:
        raise ValueError("Optical Flow grid dimensions cannot exceed image dimensions")
    y_bounds = np.linspace(0, height, grid_rows + 1, dtype=np.int64)
    x_bounds = np.linspace(0, width, grid_columns + 1, dtype=np.int64)
    result: list[FlowCell] = []
    for row in range(grid_rows):
        y0, y1 = int(y_bounds[row]), int(y_bounds[row + 1])
        for column in range(grid_columns):
            x0, x1 = int(x_bounds[column]), int(x_bounds[column + 1])
            vectors = flow[y0:y1, x0:x1]
            magnitudes = np.hypot(vectors[..., 0], vectors[..., 1])
            moving = magnitudes > minimum_motion_magnitude
            moving_count = int(np.count_nonzero(moving))
            if moving_count == 0:
                continue
            selected_vectors = vectors[moving]
            selected_magnitudes = magnitudes[moving]
            result.append(
                FlowCell(
                    row,
                    column,
                    0.0 if width == 1 else ((x0 + x1 - 1) * 0.5) / (width - 1),
                    0.0 if height == 1 else ((y0 + y1 - 1) * 0.5) / (height - 1),
                    float(np.mean(selected_vectors[:, 0], dtype=np.float64)),
                    float(np.mean(selected_vectors[:, 1], dtype=np.float64)),
                    float(np.mean(selected_magnitudes, dtype=np.float64)),
                    float(np.max(selected_magnitudes)),
                    moving_count / magnitudes.size,
                )
            )
    return tuple(result)


def create_optical_flow_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            OPTICAL_FLOW_TYPE_ID,
            1,
            "Optical Flow",
            "Synesthesia",
            (
                "Detects movement between two images, for example between the last frame and the "
                "current one, and turns it into notes. The pitch follows the direction, position, "
                "or speed of the motion, and the volume follows how strong the motion is."
            ),
            (
                InputPortSpec("current", "Current", PortType.IMAGE),
                InputPortSpec("reference", "Reference", PortType.IMAGE),
            ),
            (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
            (
                *common_musical_parameter_specs(),
                ParameterSpec(
                    "flow_preset",
                    "Flow preset",
                    PortType.STRING,
                    BALANCED,
                    help_text=(
                        "Chooses the optical-flow computation preset: Fast, Balanced, or "
                        "Accurate; faster presets are less precise but cheaper."
                    ),
                    choices=FLOW_PRESETS,
                ),
                ParameterSpec(
                    "minimum_motion_magnitude",
                    "Minimum motion magnitude",
                    PortType.FLOAT,
                    0.5,
                    help_text=(
                        "Pixels moving slower than this are treated as static and do not produce "
                        "notes."
                    ),
                    minimum=0.0,
                ),
                ParameterSpec(
                    "pitch_feature",
                    "Pitch feature",
                    PortType.STRING,
                    DIRECTION,
                    help_text=(
                        "Chooses which flow property decides the pitch: Direction of motion, its "
                        "Horizontal or Vertical position, or its Magnitude."
                    ),
                    choices=PITCH_FEATURES,
                ),
                ParameterSpec(
                    "velocity_feature",
                    "Velocity feature",
                    PortType.STRING,
                    MEAN_MAGNITUDE,
                    help_text="Chooses which flow property decides the note velocity.",
                    choices=VELOCITY_FEATURES,
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
                    "aggregation",
                    "Aggregation",
                    PortType.STRING,
                    CELL_NOTES,
                    help_text=(
                        "Chooses how the cells become notes: Cell notes plays one note per grid "
                        "cell, and Global histogram pools all cells into one histogram."
                    ),
                    choices=AGGREGATIONS,
                ),
                ParameterSpec(
                    "magnitude_minimum",
                    "Magnitude minimum",
                    PortType.FLOAT,
                    0.0,
                    help_text=("Lowest flow magnitude mapped to the minimum velocity."),
                    minimum=0.0,
                ),
                ParameterSpec(
                    "magnitude_maximum",
                    "Magnitude maximum",
                    PortType.FLOAT,
                    10.0,
                    help_text=("Highest flow magnitude mapped to the maximum velocity."),
                    minimum=0.0,
                ),
            ),
            ExecutionKind.STATELESS,
            OpticalFlowRuntime,
            aliases=("motion grid", "farneback notes", "motion to pitch"),
            parameter_validator=_validate_parameters,
            parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
        ),
    )


def _cell_candidates(
    flow: NDArray[np.float32],
    threshold: float,
    pitch_feature: str,
    velocity_feature: str,
    rows: int,
    columns: int,
    magnitude_minimum: float,
    magnitude_maximum: float,
    allowed: tuple[int, ...],
) -> list[tuple[int, float]]:
    candidates: list[tuple[int, float]] = []
    for cell in extract_flow_cells(
        flow,
        grid_rows=rows,
        grid_columns=columns,
        minimum_motion_magnitude=threshold,
    ):
        pitch = cell.pitch_value(pitch_feature, magnitude_minimum, magnitude_maximum)
        candidates.append(
            (
                allowed[_note_index(pitch, len(allowed))],
                cell.velocity_strength(velocity_feature, magnitude_minimum, magnitude_maximum),
            )
        )
    return candidates


def _histogram_candidates(
    flow: NDArray[np.float32],
    threshold: float,
    pitch_feature: str,
    velocity_feature: str,
    magnitude_minimum: float,
    magnitude_maximum: float,
    allowed: tuple[int, ...],
) -> list[tuple[int, float]]:
    height, width = flow.shape[:2]
    magnitudes = np.hypot(flow[..., 0], flow[..., 1])
    moving = magnitudes > threshold
    if not np.any(moving):
        return []
    if pitch_feature == DIRECTION:
        pitches = np.mod(np.arctan2(flow[..., 1], flow[..., 0]), 2.0 * np.pi) / (2.0 * np.pi)
    elif pitch_feature == HORIZONTAL_POSITION:
        horizontal = (
            np.zeros(width) if width == 1 else np.arange(width, dtype=np.float64) / (width - 1)
        )
        pitches = np.broadcast_to(horizontal, (height, width))
    elif pitch_feature == VERTICAL_POSITION:
        vertical = (
            np.zeros(height) if height == 1 else np.arange(height, dtype=np.float64) / (height - 1)
        )
        pitches = np.broadcast_to(vertical[:, None], (height, width))
    else:
        pitches = np.clip(
            (magnitudes - magnitude_minimum) / (magnitude_maximum - magnitude_minimum), 0.0, 1.0
        )
    indexes = np.floor(np.clip(pitches, 0.0, 1.0) * (len(allowed) - 1) + 0.5).astype(np.int64)
    candidates: list[tuple[int, float]] = []
    for index, note in enumerate(allowed):
        selected = moving & (indexes == index)
        count = int(np.count_nonzero(selected))
        if count == 0:
            continue
        selected_magnitudes = magnitudes[selected]
        if velocity_feature == MEAN_MAGNITUDE:
            strength = _normalize(
                float(np.mean(selected_magnitudes, dtype=np.float64)),
                magnitude_minimum,
                magnitude_maximum,
            )
        elif velocity_feature == MAXIMUM_MAGNITUDE:
            strength = _normalize(
                float(np.max(selected_magnitudes)), magnitude_minimum, magnitude_maximum
            )
        else:
            strength = count / magnitudes.size
        candidates.append((note, strength))
    return candidates


def _luminance_uint8(image: ImageFrame) -> NDArray[np.uint8]:
    luminance = image_to_luminance(image).data
    return np.ascontiguousarray(np.rint(np.clip(luminance, 0.0, 1.0) * 255.0), dtype=np.uint8)


def _validate_flow(flow: NDArray[np.float32]) -> None:
    if flow.dtype != np.float32 or flow.ndim != 3 or flow.shape[2] != 2:
        raise ValueError("Optical Flow vector field must be float32 with shape (height, width, 2)")
    if flow.shape[0] < 1 or flow.shape[1] < 1 or not np.isfinite(flow).all():
        raise ValueError("Optical Flow vector field must be non-empty and finite")


def _direction(dx: float, dy: float) -> float:
    return (math.atan2(dy, dx) % (2.0 * math.pi)) / (2.0 * math.pi)


def _note_index(normalized: float, note_count: int) -> int:
    return 0 if note_count == 1 else math.floor(normalized * (note_count - 1) + 0.5)


def _normalize(value: float, minimum: float, maximum: float) -> float:
    return min(1.0, max(0.0, (value - minimum) / (maximum - minimum)))


def _validate_algorithm_parameters(
    *,
    minimum_motion_magnitude: float,
    pitch_feature: str,
    velocity_feature: str,
    grid_rows: int,
    grid_columns: int,
    aggregation: str,
    magnitude_minimum: float,
    magnitude_maximum: float,
) -> None:
    _require_non_negative(minimum_motion_magnitude, "Minimum motion magnitude")
    if pitch_feature not in PITCH_FEATURES:
        raise ValueError(f"Unknown optical-flow pitch feature: {pitch_feature!r}")
    if velocity_feature not in VELOCITY_FEATURES:
        raise ValueError(f"Unknown optical-flow velocity feature: {velocity_feature!r}")
    if grid_rows < 1 or grid_columns < 1:
        raise ValueError("Optical Flow grid dimensions must be at least one")
    if aggregation not in AGGREGATIONS:
        raise ValueError(f"Unknown optical-flow aggregation: {aggregation!r}")
    if (
        not math.isfinite(magnitude_minimum)
        or not math.isfinite(magnitude_maximum)
        or magnitude_minimum < 0.0
        or magnitude_maximum <= magnitude_minimum
    ):
        raise ValueError("Optical Flow magnitude maximum must be finite and greater than minimum")


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    try:
        _validate_algorithm_parameters(
            minimum_motion_magnitude=_number(parameters["minimum_motion_magnitude"]),
            pitch_feature=_text(parameters["pitch_feature"]),
            velocity_feature=_text(parameters["velocity_feature"]),
            grid_rows=_integer(parameters["grid_rows"]),
            grid_columns=_integer(parameters["grid_columns"]),
            aggregation=_text(parameters["aggregation"]),
            magnitude_minimum=_number(parameters["magnitude_minimum"]),
            magnitude_maximum=_number(parameters["magnitude_maximum"]),
        )
        if _text(parameters["flow_preset"]) not in FLOW_PRESETS:
            raise ValueError(f"Unknown optical-flow preset: {parameters['flow_preset']!r}")
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
    return errors


def _require_non_negative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")


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
    "ACCURATE",
    "BALANCED",
    "CELL_NOTES",
    "DIRECTION",
    "FARNEBACK_PRESETS",
    "FAST",
    "GLOBAL_HISTOGRAM",
    "HORIZONTAL_POSITION",
    "MAGNITUDE",
    "MAXIMUM_MAGNITUDE",
    "MEAN_MAGNITUDE",
    "MOVING_PIXEL_FRACTION",
    "OPTICAL_FLOW_TYPE_ID",
    "VERTICAL_POSITION",
    "FarnebackPreset",
    "FlowCell",
    "OpticalFlowRuntime",
    "calculate_dense_flow",
    "create_optical_flow_definitions",
    "extract_flow_cells",
    "flow_to_midi_state",
    "optical_flow_to_midi_state",
]
