"""Deterministic contour-feature mapping from edge channels to MIDI state."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
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
)
from synesthesia_machine.nodes.synesthesia.musical import (
    COMMON_MUSICAL_PARAMETER_GROUP,
    CommonMusicalSettings,
    common_musical_parameter_specs,
    midi_state_from_candidates,
    resolve_common_musical_settings,
    validate_common_musical_parameters,
)

EDGES_TO_PITCH_TYPE_ID = "synmachine.synesthesia.edges_to_pitch"
EXTERNAL = "EXTERNAL"
TREE = "TREE"
PERIMETER = "PERIMETER"
AREA = "AREA"
CENTROID_X = "CENTROID_X"
CENTROID_Y = "CENTROID_Y"
ORIENTATION = "ORIENTATION"
CIRCULARITY = "CIRCULARITY"
EDGE_STRENGTH = "EDGE_STRENGTH"

PITCH_FEATURES = (PERIMETER, AREA, CENTROID_X, CENTROID_Y, ORIENTATION, CIRCULARITY)
VELOCITY_FEATURES = (*PITCH_FEATURES, EDGE_STRENGTH)


@dataclass(frozen=True, slots=True)
class ContourFeatures:
    """Finite contour measurements in pixels or documented fixed normalized ranges."""

    perimeter: float
    area: float
    centroid_x: float
    centroid_y: float
    orientation: float
    circularity: float
    edge_strength: float

    def value(self, feature: str) -> float:
        return {
            PERIMETER: self.perimeter,
            AREA: self.area,
            CENTROID_X: self.centroid_x,
            CENTROID_Y: self.centroid_y,
            ORIENTATION: self.orientation,
            CIRCULARITY: self.circularity,
            EDGE_STRENGTH: self.edge_strength,
        }[feature]


class EdgesToPitchRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            midi = edges_to_midi_state(
                _channel(inputs["edges"]),
                retrieval_mode=_text(parameters["retrieval_mode"]),
                minimum_contour_area=_number(parameters["minimum_contour_area"]),
                minimum_contour_perimeter=_number(parameters["minimum_contour_perimeter"]),
                contour_limit=_integer(parameters["contour_limit"]),
                pitch_feature=_text(parameters["pitch_feature"]),
                velocity_feature=_text(parameters["velocity_feature"]),
                pitch_minimum=_number(parameters["pitch_minimum"]),
                pitch_maximum=_number(parameters["pitch_maximum"]),
                velocity_minimum=_number(parameters["velocity_minimum"]),
                velocity_maximum=_number(parameters["velocity_maximum"]),
                settings=resolve_common_musical_settings(parameters),
                node_id=self.node_id,
                context=context,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_edges_to_pitch", str(error)) from error
        return {"midi": midi}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def edges_to_midi_state(
    edges: ChannelFrame,
    *,
    retrieval_mode: str,
    minimum_contour_area: float,
    minimum_contour_perimeter: float,
    contour_limit: int,
    pitch_feature: str,
    velocity_feature: str,
    pitch_minimum: float,
    pitch_maximum: float,
    velocity_minimum: float,
    velocity_maximum: float,
    settings: CommonMusicalSettings,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Map filtered contour features through explicit ranges into desired MIDI state."""

    if edges.context.clock_id != context.clock_id:
        raise ValueError("Edges to Pitch channel clock does not match the execution clock")
    _validate_feature(pitch_feature, PITCH_FEATURES, "pitch")
    _validate_feature(velocity_feature, VELOCITY_FEATURES, "velocity")
    _validate_range(pitch_minimum, pitch_maximum, "pitch")
    _validate_range(velocity_minimum, velocity_maximum, "velocity")
    features = extract_contour_features(
        edges,
        retrieval_mode=retrieval_mode,
        minimum_contour_area=minimum_contour_area,
        minimum_contour_perimeter=minimum_contour_perimeter,
        contour_limit=contour_limit,
    )
    allowed = settings.selector.allowed_notes
    candidates: list[tuple[int, float]] = []
    for contour in features:
        pitch = _normalize(contour.value(pitch_feature), pitch_minimum, pitch_maximum)
        index = 0 if len(allowed) == 1 else math.floor(pitch * (len(allowed) - 1) + 0.5)
        strength = _normalize(contour.value(velocity_feature), velocity_minimum, velocity_maximum)
        candidates.append((allowed[index], strength))
    return midi_state_from_candidates(
        candidates,
        settings=settings,
        context=context,
        source_node_id=node_id,
    )


def extract_contour_features(
    edges: ChannelFrame,
    *,
    retrieval_mode: str,
    minimum_contour_area: float,
    minimum_contour_perimeter: float,
    contour_limit: int,
) -> tuple[ContourFeatures, ...]:
    """Extract spatially ordered, finite, non-degenerate contour measurements."""

    if retrieval_mode not in (EXTERNAL, TREE):
        raise ValueError(f"Unknown contour retrieval mode: {retrieval_mode!r}")
    _require_non_negative(minimum_contour_area, "Minimum contour area")
    _require_non_negative(minimum_contour_perimeter, "Minimum contour perimeter")
    if contour_limit < 1:
        raise ValueError("Contour limit must be at least one")

    normalized = (edges.data - edges.nominal_min) / (edges.nominal_max - edges.nominal_min)
    normalized = np.asarray(
        np.clip(np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0),
        dtype=np.float32,
    )
    mask = np.asarray(normalized > 0.0, dtype=np.uint8) * np.uint8(255)
    mode = cv2.RETR_EXTERNAL if retrieval_mode == EXTERNAL else cv2.RETR_TREE
    try:
        raw_contours, _ = cv2.findContours(mask, mode, cv2.CHAIN_APPROX_NONE)
    except cv2.error as error:
        raise ValueError(f"Contour extraction failed: {error}") from error
    contours = [cast(NDArray[np.int32], contour) for contour in raw_contours]
    contours.sort(key=_contour_sort_key)

    height, width = normalized.shape
    result: list[ContourFeatures] = []
    for contour in contours[:contour_limit]:
        perimeter = float(cv2.arcLength(contour, True))
        area = float(abs(cv2.contourArea(contour)))
        if area < minimum_contour_area or perimeter < minimum_contour_perimeter:
            continue
        moments = cv2.moments(contour)
        mass = float(moments["m00"])
        if not math.isfinite(mass) or abs(mass) <= np.finfo(np.float64).eps or perimeter <= 0.0:
            continue
        centroid_x_pixels = float(moments["m10"]) / mass
        centroid_y_pixels = float(moments["m01"]) / mass
        angle = 0.5 * math.atan2(
            2.0 * float(moments["mu11"]),
            float(moments["mu20"]) - float(moments["mu02"]),
        )
        orientation = math.degrees(angle % math.pi)
        circularity = min(1.0, max(0.0, 4.0 * math.pi * area / (perimeter * perimeter)))
        points = contour.reshape(-1, 2)
        x = np.clip(points[:, 0], 0, width - 1)
        y = np.clip(points[:, 1], 0, height - 1)
        edge_strength = float(np.mean(normalized[y, x], dtype=np.float64))
        values = (
            perimeter,
            area,
            centroid_x_pixels,
            centroid_y_pixels,
            orientation,
            circularity,
            edge_strength,
        )
        if not all(math.isfinite(value) for value in values):
            continue
        result.append(
            ContourFeatures(
                perimeter,
                area,
                0.0 if width == 1 else centroid_x_pixels / (width - 1),
                0.0 if height == 1 else centroid_y_pixels / (height - 1),
                orientation,
                circularity,
                edge_strength,
            )
        )
    return tuple(result)


def create_edges_to_pitch_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            EDGES_TO_PITCH_TYPE_ID,
            1,
            "Edges to Pitch",
            "Synesthesia",
            (
                "Finds the shapes in an edge or mask channel and turns each one into a note. You "
                "choose which property of the shape (its position, size, roundness, or "
                "orientation) sets the pitch and which sets the volume, which can also follow "
                "edge strength. Shapes smaller than your limits are ignored."
            ),
            (InputPortSpec("edges", "Edges", PortType.CHANNEL),),
            (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
            (
                *common_musical_parameter_specs(),
                ParameterSpec(
                    "minimum_contour_area", "Minimum contour area", PortType.FLOAT, 1.0, minimum=0.0
                ),
                ParameterSpec(
                    "minimum_contour_perimeter",
                    "Minimum contour perimeter",
                    PortType.FLOAT,
                    1.0,
                    minimum=0.0,
                ),
                ParameterSpec(
                    "retrieval_mode",
                    "Retrieval mode",
                    PortType.STRING,
                    EXTERNAL,
                    choices=(EXTERNAL, TREE),
                ),
                ParameterSpec(
                    "pitch_feature",
                    "Pitch feature",
                    PortType.STRING,
                    CENTROID_X,
                    choices=PITCH_FEATURES,
                ),
                ParameterSpec(
                    "velocity_feature",
                    "Velocity feature",
                    PortType.STRING,
                    EDGE_STRENGTH,
                    choices=VELOCITY_FEATURES,
                ),
                ParameterSpec("pitch_minimum", "Pitch minimum", PortType.FLOAT, 0.0),
                ParameterSpec("pitch_maximum", "Pitch maximum", PortType.FLOAT, 1.0),
                ParameterSpec("velocity_minimum", "Velocity minimum", PortType.FLOAT, 0.0),
                ParameterSpec("velocity_maximum", "Velocity maximum", PortType.FLOAT, 1.0),
                ParameterSpec(
                    "contour_limit", "Contour limit", PortType.INT, 128, minimum=1, maximum=4096
                ),
            ),
            ExecutionKind.STATELESS,
            EdgesToPitchRuntime,
            aliases=("contours to notes", "edge notes", "shape to pitch"),
            parameter_validator=_validate_parameters,
            parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
        ),
    )


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    try:
        _validate_range(
            _number(parameters["pitch_minimum"]), _number(parameters["pitch_maximum"]), "pitch"
        )
        _validate_range(
            _number(parameters["velocity_minimum"]),
            _number(parameters["velocity_maximum"]),
            "velocity",
        )
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
    return errors


def _contour_sort_key(contour: NDArray[np.int32]) -> tuple[int, int, int, int, int]:
    x, y, width, height = cv2.boundingRect(contour)
    return (y, x, height, width, contour.shape[0])


def _normalize(value: float, minimum: float, maximum: float) -> float:
    return min(1.0, max(0.0, (value - minimum) / (maximum - minimum)))


def _validate_feature(feature: str, choices: tuple[str, ...], role: str) -> None:
    if feature not in choices:
        raise ValueError(f"Unknown contour {role} feature: {feature!r}")


def _validate_range(minimum: float, maximum: float, role: str) -> None:
    if not math.isfinite(minimum) or not math.isfinite(maximum) or maximum <= minimum:
        raise ValueError(f"Contour {role} maximum must be finite and greater than minimum")


def _require_non_negative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")


def _channel(value: object) -> ChannelFrame:
    if isinstance(value, ChannelFrame):
        return value
    raise TypeError(f"Expected ChannelFrame, got {type(value).__name__}")


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
    "AREA",
    "CENTROID_X",
    "CENTROID_Y",
    "CIRCULARITY",
    "EDGES_TO_PITCH_TYPE_ID",
    "EDGE_STRENGTH",
    "EXTERNAL",
    "ORIENTATION",
    "PERIMETER",
    "TREE",
    "ContourFeatures",
    "EdgesToPitchRuntime",
    "create_edges_to_pitch_definitions",
    "edges_to_midi_state",
    "extract_contour_features",
]
