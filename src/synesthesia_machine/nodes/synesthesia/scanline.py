"""Stateful horizontal scanline mapping from channel samples to MIDI state."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

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
    NodeExecutionContract,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    ResetReason,
    StatelessRuntime,
)
from synesthesia_machine.nodes.synesthesia.musical import (
    COMMON_MUSICAL_PARAMETER_GROUP,
    CommonMusicalSettings,
    common_musical_parameter_specs,
    midi_state_from_candidates,
    resolve_common_musical_settings,
    validate_common_musical_parameters,
)

SCANLINE_TYPE_ID = "synmachine.synesthesia.scanline"
BOTTOM_TO_TOP = "BOTTOM_TO_TOP"
TOP_TO_BOTTOM = "TOP_TO_BOTTOM"
PING_PONG = "PING_PONG"
MEAN = "MEAN"
MAXIMUM = "MAXIMUM"
VALUE = "VALUE"
CONTRAST = "CONTRAST"
SCANLINE_METRICS = (VALUE, CONTRAST)


class ScanlineRuntime(StatelessRuntime):
    """Own scan position and advance it once per successful processed tick."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        self._row_index: int | None = None
        self._height: int | None = None
        self._direction: str | None = None
        self._ping_pong_step = 1

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            channel = cast(ChannelFrame, inputs["value"])
            direction = cast(str, parameters["direction"])
            advance_rows = cast(int, parameters["advance_rows"])
            _validate_direction(direction)
            _validate_advance_rows(advance_rows)
            height = channel.data.shape[0]
            if self._row_index is None or self._height != height or self._direction != direction:
                self._initialize(height, direction)
            assert self._row_index is not None
            settings = resolve_common_musical_settings(parameters)
            midi = scanline_to_midi_state(
                channel,
                row_index=self._row_index,
                line_thickness=cast(int, parameters["line_thickness"]),
                metric=cast(str, parameters["metric"]),
                aggregation=cast(str, parameters["aggregation"]),
                activation_threshold=cast(float, parameters["activation_threshold"]),
                velocity_curve_exponent=cast(float, parameters["velocity_curve_exponent"]),
                settings=settings,
                node_id=self.node_id,
                context=context,
            )
            self._advance(height, direction, advance_rows)
        except (KeyError, TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_scanline", str(error)) from error
        return {"midi": midi}

    def reset(self, reason: ResetReason) -> None:
        del reason
        self._clear()

    def close(self) -> None:
        self._clear()

    def _initialize(self, height: int, direction: str) -> None:
        if height < 1:
            raise ValueError("Scanline channel height must be at least one")
        _validate_direction(direction)
        self._height = height
        self._direction = direction
        self._ping_pong_step = 1
        self._row_index = height - 1 if direction == BOTTOM_TO_TOP else 0

    def _advance(self, height: int, direction: str, advance_rows: int) -> None:
        assert self._row_index is not None
        _validate_direction(direction)
        _validate_advance_rows(advance_rows)
        if direction == TOP_TO_BOTTOM:
            self._row_index = (self._row_index + advance_rows) % height
            return
        if direction == BOTTOM_TO_TOP:
            self._row_index = (self._row_index - advance_rows) % height
            return
        if height == 1:
            self._row_index = 0
            return
        for _ in range(advance_rows):
            candidate = self._row_index + self._ping_pong_step
            if candidate >= height:
                self._ping_pong_step = -1
                candidate = height - 2
            elif candidate < 0:
                self._ping_pong_step = 1
                candidate = 1
            self._row_index = candidate

    def _clear(self) -> None:
        self._row_index = None
        self._height = None
        self._direction = None
        self._ping_pong_step = 1


def scanline_to_midi_state(
    value: ChannelFrame,
    *,
    row_index: int,
    line_thickness: int,
    metric: str,
    aggregation: str,
    activation_threshold: float,
    velocity_curve_exponent: float,
    settings: CommonMusicalSettings,
    node_id: UUID,
    context: FrameContext,
) -> MidiStateFrame:
    """Map one horizontal row band to ordered allowed notes."""

    if value.context.clock_id != context.clock_id:
        raise ValueError("Scanline channel clock does not match the execution clock")
    height = value.data.shape[0]
    if not 0 <= row_index < height:
        raise ValueError("Scanline row index is outside the channel height")
    if line_thickness < 1:
        raise ValueError("Scanline line thickness must be at least one")
    if aggregation not in (MEAN, MAXIMUM):
        raise ValueError(f"Unknown scanline aggregation: {aggregation!r}")
    if metric not in SCANLINE_METRICS:
        raise ValueError(f"Unknown scanline metric: {metric!r}")
    if not math.isfinite(activation_threshold) or not 0.0 <= activation_threshold <= 1.0:
        raise ValueError("Scanline activation threshold must be finite and in the range 0..1")
    if not math.isfinite(velocity_curve_exponent) or velocity_curve_exponent <= 0.0:
        raise ValueError("Scanline velocity curve exponent must be finite and positive")

    before = (line_thickness - 1) // 2
    after = line_thickness // 2
    band = value.data[max(0, row_index - before) : min(height, row_index + after + 1)]
    if metric == CONTRAST:
        # Per-column row-to-row variation across the band. The band is
        # normalized through its nominal range before the population
        # standard deviation so the 2 x std bound of 1 holds for any nominal
        # range, mirroring Region Grid's Contrast metric. Aggregation is
        # irrelevant for a variance measure.
        band = np.asarray(band, dtype=np.float64)
        normalized = (band - value.nominal_min) / (value.nominal_max - value.nominal_min)
        normalized = np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0)
        normalized = np.asarray(np.clip(normalized, 0.0, 1.0), dtype=np.float64)
        contrast = 2.0 * normalized.std(axis=0, dtype=np.float64)
        normalized = np.asarray(np.clip(contrast, 0.0, 1.0), dtype=np.float64)
    else:
        line = (
            np.mean(band, axis=0, dtype=np.float64) if aggregation == MEAN else np.max(band, axis=0)
        )
        normalized = (line - value.nominal_min) / (value.nominal_max - value.nominal_min)
        normalized = np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0)
        normalized = np.asarray(np.clip(normalized, 0.0, 1.0), dtype=np.float64)
    samples = _resize_area_1d(normalized, len(settings.selector.allowed_notes))

    candidates: list[tuple[int, float]] = []
    for note, sample in zip(settings.selector.allowed_notes, samples, strict=True):
        strength = _sample_strength(float(sample), activation_threshold)
        if strength is not None:
            candidates.append((note, strength**velocity_curve_exponent))
    return midi_state_from_candidates(
        candidates,
        settings=settings,
        context=context,
        source_node_id=node_id,
    )


def create_scanline_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            execution=NodeExecutionContract(
                SCANLINE_TYPE_ID,
                1,
                ExecutionKind.STATEFUL,
                (InputPortSpec("value", "Value", PortType.CHANNEL),),
                (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
                (
                    *common_musical_parameter_specs(),
                    ParameterSpec(
                        "direction",
                        "Direction",
                        PortType.STRING,
                        BOTTOM_TO_TOP,
                        help_text=(
                            "Chooses how the scan line moves across the image: bottom to top, top "
                            "to "
                            "bottom, or ping pong."
                        ),
                        choices=(BOTTOM_TO_TOP, TOP_TO_BOTTOM, PING_PONG),
                    ),
                    ParameterSpec(
                        "advance_rows",
                        "Advance rows",
                        PortType.INT,
                        1,
                        help_text=(
                            "Rows the scan line moves per frame; larger values move faster across "
                            "the "
                            "image."
                        ),
                        minimum=1,
                        maximum=8192,
                    ),
                    ParameterSpec(
                        "line_thickness",
                        "Line thickness",
                        PortType.INT,
                        1,
                        help_text=(
                            "Number of rows read at once under the scan line; thicker lines are "
                            "less "
                            "sensitive to noise."
                        ),
                        minimum=1,
                        maximum=8192,
                    ),
                    ParameterSpec(
                        "metric",
                        "Scan metric",
                        PortType.STRING,
                        VALUE,
                        help_text=(
                            "Selects what is measured under the scan line: Value reads the "
                            "normalized "
                            "channel samples; Contrast measures the normalized row-to-row "
                            "variation "
                            "inside the line band, ignores Aggregation, and needs a line thicker "
                            "than "
                            "one row to be non-zero."
                        ),
                        choices=SCANLINE_METRICS,
                    ),
                    ParameterSpec(
                        "aggregation",
                        "Aggregation",
                        PortType.STRING,
                        MEAN,
                        help_text=(
                            "Chooses how the values under the line are combined: Mean averages "
                            "them, "
                            "and Maximum uses the strongest."
                        ),
                        choices=(MEAN, MAXIMUM),
                    ),
                    ParameterSpec(
                        "activation_threshold",
                        "Activation threshold",
                        PortType.FLOAT,
                        0.0,
                        help_text=(
                            "Rows below this normalized value stay silent; stronger rows play "
                            "notes."
                        ),
                        minimum=0.0,
                        maximum=1.0,
                        editor_hint=ParameterEditorHint.SLIDER,
                    ),
                    ParameterSpec(
                        "velocity_curve_exponent",
                        "Velocity curve exponent",
                        PortType.FLOAT,
                        1.0,
                        help_text=(
                            "Shapes the velocity response; values above 1 make quiet rows quieter "
                            "and "
                            "loud rows louder."
                        ),
                        minimum=0.01,
                        maximum=16.0,
                        editor_hint=ParameterEditorHint.SLIDER,
                    ),
                ),
                ScanlineRuntime,
                parameter_validator=_validate_parameters,
            ),
            presentation=NodePresentationIntent(
                "Scanline",
                "Synesthesia",
                "Sends a line across the picture, like a scanner, and turns each row it passes "
                "into notes from left to right; stronger values play louder. You choose the "
                "sweep "
                "direction — up, down, or back and forth — and how fast the line advances. A "
                "reset sends the line back to the start.",
                aliases=("scanning score", "row scanner", "image scan sequencer"),
                parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
            ),
        ),
    )


def _resize_area_1d(values: NDArray[np.float64], output_count: int) -> NDArray[np.float64]:
    if values.ndim != 1 or values.size < 1 or output_count < 1:
        raise ValueError("Scanline area resize requires non-empty one-dimensional samples")
    cumulative = np.concatenate((np.array([0.0]), np.cumsum(values, dtype=np.float64)))
    boundaries = np.linspace(0.0, float(values.size), output_count + 1)
    integrals = np.interp(boundaries, np.arange(values.size + 1), cumulative)
    resized = np.diff(integrals) / (values.size / output_count)
    return np.asarray(np.clip(resized, 0.0, 1.0), dtype=np.float64)


def _sample_strength(sample: float, threshold: float) -> float | None:
    if threshold == 1.0:
        return 1.0 if sample == 1.0 else None
    if sample <= threshold:
        return None
    return (sample - threshold) / (1.0 - threshold)


def _validate_direction(direction: str) -> None:
    if direction not in (BOTTOM_TO_TOP, TOP_TO_BOTTOM, PING_PONG):
        raise ValueError(f"Unknown scanline direction: {direction!r}")


def _validate_advance_rows(advance_rows: int) -> None:
    if advance_rows < 1:
        raise ValueError("Scanline advance rows must be at least one")


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    try:
        metric = cast(str, parameters["metric"])
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
        return errors
    if metric not in SCANLINE_METRICS:
        errors.append(f"Unknown scanline metric: {metric!r}")
    return errors


__all__ = [
    "BOTTOM_TO_TOP",
    "CONTRAST",
    "MAXIMUM",
    "MEAN",
    "PING_PONG",
    "SCANLINE_METRICS",
    "SCANLINE_TYPE_ID",
    "TOP_TO_BOTTOM",
    "VALUE",
    "ScanlineRuntime",
    "create_scanline_definitions",
    "scanline_to_midi_state",
]
