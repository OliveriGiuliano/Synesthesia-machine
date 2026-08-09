"""Deterministic spatial-frequency mapping with a reusable shape-dependent band map."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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

FOURIER_TYPE_ID = "synmachine.synesthesia.fourier"
NONE = "NONE"
HANN = "HANN"
HAMMING = "HAMMING"
RADIAL_MAGNITUDE = "RADIAL_MAGNITUDE"
HORIZONTAL = "HORIZONTAL"
VERTICAL = "VERTICAL"
BAND_MEAN = "MEAN"
BAND_PERCENTILE = "PERCENTILE"

WINDOWS = (NONE, HANN, HAMMING)
FREQUENCY_MAPPINGS = (RADIAL_MAGNITUDE, HORIZONTAL, VERTICAL)
BAND_AGGREGATIONS = (BAND_MEAN, BAND_PERCENTILE)


@dataclass(frozen=True, slots=True)
class FourierBandMapKey:
    """Every setting that changes assignment of FFT cells to musical bands."""

    shape: tuple[int, int]
    note_count: int
    frequency_mapping: str
    frequency_minimum: float
    frequency_maximum: float
    dc_exclusion_radius: float


class FourierShapeCache:
    """Own one read-only radial/index map and reuse it while its complete key is stable."""

    def __init__(self) -> None:
        self._key: FourierBandMapKey | None = None
        self._band_indexes: NDArray[np.int16] | None = None
        self._hits = 0
        self._misses = 0

    @property
    def key(self) -> FourierBandMapKey | None:
        return self._key

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def has_entry(self) -> bool:
        return self._band_indexes is not None

    def resolve(self, key: FourierBandMapKey) -> NDArray[np.int16]:
        if self._key == key and self._band_indexes is not None:
            self._hits += 1
            return self._band_indexes
        band_indexes = build_fourier_band_map(key)
        self._key = key
        self._band_indexes = band_indexes
        self._misses += 1
        return band_indexes

    def clear(self) -> None:
        self._key = None
        self._band_indexes = None


class FourierRuntime:
    """Own the shape-dependent map cache without making output history-dependent."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        self.cache = FourierShapeCache()

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            midi = fourier_to_midi_state(
                _channel(inputs["value"]),
                window=_text(parameters["window"]),
                subtract_mean=_boolean(parameters["subtract_mean"]),
                frequency_mapping=_text(parameters["frequency_mapping"]),
                frequency_minimum=_number(parameters["frequency_minimum"]),
                frequency_maximum=_number(parameters["frequency_maximum"]),
                amplitude_floor=_number(parameters["amplitude_floor"]),
                amplitude_ceiling=_number(parameters["amplitude_ceiling"]),
                dc_exclusion_radius=_number(parameters["dc_exclusion_radius"]),
                band_aggregation=_text(parameters["band_aggregation"]),
                percentile=_number(parameters["percentile"]),
                activation_threshold=_number(parameters["activation_threshold"]),
                settings=resolve_common_musical_settings(parameters),
                node_id=self.node_id,
                context=context,
                cache=self.cache,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_fourier", str(error)) from error
        return {"midi": midi}

    def reset(self, reason: ResetReason) -> None:
        del reason
        self.cache.clear()

    def close(self) -> None:
        self.cache.clear()


def fourier_to_midi_state(
    value: ChannelFrame,
    *,
    window: str,
    subtract_mean: bool,
    frequency_mapping: str,
    frequency_minimum: float,
    frequency_maximum: float,
    amplitude_floor: float,
    amplitude_ceiling: float,
    dc_exclusion_radius: float,
    band_aggregation: str,
    percentile: float,
    activation_threshold: float,
    settings: CommonMusicalSettings,
    node_id: UUID,
    context: FrameContext,
    cache: FourierShapeCache | None = None,
) -> MidiStateFrame:
    """Map fixed spatial-frequency bands to ordered allowed notes and strengths."""

    if value.context.clock_id != context.clock_id:
        raise ValueError("Fourier channel clock does not match the execution clock")
    _validate_algorithm_parameters(
        window=window,
        frequency_mapping=frequency_mapping,
        frequency_minimum=frequency_minimum,
        frequency_maximum=frequency_maximum,
        amplitude_floor=amplitude_floor,
        amplitude_ceiling=amplitude_ceiling,
        dc_exclusion_radius=dc_exclusion_radius,
        band_aggregation=band_aggregation,
        percentile=percentile,
        activation_threshold=activation_threshold,
    )

    note_count = len(settings.selector.allowed_notes)
    key = FourierBandMapKey(
        value.data.shape,
        note_count,
        frequency_mapping,
        frequency_minimum,
        frequency_maximum,
        dc_exclusion_radius,
    )
    band_indexes = build_fourier_band_map(key) if cache is None else cache.resolve(key)
    amplitudes = fourier_band_amplitudes(
        value,
        band_indexes=band_indexes,
        note_count=note_count,
        window=window,
        subtract_mean=subtract_mean,
        band_aggregation=band_aggregation,
        percentile=percentile,
    )
    candidates: list[tuple[int, float]] = []
    for note, amplitude in zip(settings.selector.allowed_notes, amplitudes, strict=True):
        strength = _amplitude_strength(
            float(amplitude), amplitude_floor, amplitude_ceiling, activation_threshold
        )
        if strength is not None:
            candidates.append((note, strength))
    return midi_state_from_candidates(
        candidates,
        settings=settings,
        context=context,
        source_node_id=node_id,
    )


def fourier_band_amplitudes(
    value: ChannelFrame,
    *,
    band_indexes: NDArray[np.int16],
    note_count: int,
    window: str,
    subtract_mean: bool,
    band_aggregation: str,
    percentile: float,
) -> NDArray[np.float64]:
    """Return one deterministic log-magnitude aggregate per cached frequency band."""

    if band_indexes.shape != value.data.shape:
        raise ValueError("Fourier band map shape must match the channel shape")
    if note_count < 1:
        raise ValueError("Fourier note count must be at least one")
    _validate_window(window)
    _validate_aggregation(band_aggregation, percentile)

    normalized = (value.data - value.nominal_min) / (value.nominal_max - value.nominal_min)
    samples = np.asarray(
        np.clip(np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0),
        dtype=np.float64,
    )
    if subtract_mean:
        samples = samples - float(np.mean(samples, dtype=np.float64))
    samples *= _window_2d(samples.shape, window)
    log_magnitude = np.log1p(np.abs(np.fft.fft2(samples)))

    amplitudes = np.zeros(note_count, dtype=np.float64)
    for band in range(note_count):
        selected = log_magnitude[band_indexes == band]
        if selected.size == 0:
            continue
        amplitudes[band] = (
            float(np.mean(selected, dtype=np.float64))
            if band_aggregation == BAND_MEAN
            else float(np.percentile(selected, percentile))
        )
    return amplitudes


def build_fourier_band_map(key: FourierBandMapKey) -> NDArray[np.int16]:
    """Build one read-only full-FFT index map; -1 marks excluded cells."""

    height, width = key.shape
    if height < 1 or width < 1:
        raise ValueError("Fourier channel dimensions must be at least one")
    if key.note_count < 1 or key.note_count > np.iinfo(np.int16).max:
        raise ValueError("Fourier note count is outside the supported range")
    _validate_frequency_parameters(
        key.frequency_mapping,
        key.frequency_minimum,
        key.frequency_maximum,
        key.dc_exclusion_radius,
    )

    vertical = np.abs(np.fft.fftfreq(height)) * 2.0
    horizontal = np.abs(np.fft.fftfreq(width)) * 2.0
    horizontal_grid, vertical_grid = np.meshgrid(horizontal, vertical)
    radial = np.hypot(horizontal_grid, vertical_grid) / math.sqrt(2.0)
    coordinates = {
        RADIAL_MAGNITUDE: radial,
        HORIZONTAL: horizontal_grid,
        VERTICAL: vertical_grid,
    }[key.frequency_mapping]
    valid = (coordinates >= key.frequency_minimum) & (coordinates <= key.frequency_maximum)
    if key.dc_exclusion_radius > 0.0:
        valid &= radial > key.dc_exclusion_radius
    scaled = (coordinates - key.frequency_minimum) / (key.frequency_maximum - key.frequency_minimum)
    assigned = np.floor(scaled * key.note_count).astype(np.int64)
    np.clip(assigned, 0, key.note_count - 1, out=assigned)
    result = np.full(key.shape, -1, dtype=np.int16)
    result[valid] = assigned[valid].astype(np.int16)
    result = np.ascontiguousarray(result)
    result.setflags(write=False)
    return result


def create_fourier_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            FOURIER_TYPE_ID,
            1,
            "Fourier",
            "Synesthesia",
            "Map cached spatial-frequency bands into scale-constrained desired MIDI state.",
            (InputPortSpec("value", "Value", PortType.CHANNEL),),
            (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
            (
                *common_musical_parameter_specs(),
                ParameterSpec("window", "Window", PortType.STRING, HANN, choices=WINDOWS),
                ParameterSpec("subtract_mean", "Subtract mean", PortType.BOOL, True),
                ParameterSpec(
                    "frequency_mapping",
                    "Frequency mapping",
                    PortType.STRING,
                    RADIAL_MAGNITUDE,
                    choices=FREQUENCY_MAPPINGS,
                ),
                ParameterSpec(
                    "frequency_minimum",
                    "Minimum normalized frequency",
                    PortType.FLOAT,
                    0.0,
                    minimum=0.0,
                    maximum=1.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "frequency_maximum",
                    "Maximum normalized frequency",
                    PortType.FLOAT,
                    1.0,
                    minimum=0.0,
                    maximum=1.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec("amplitude_floor", "Amplitude floor", PortType.FLOAT, 0.0),
                ParameterSpec("amplitude_ceiling", "Amplitude ceiling", PortType.FLOAT, 10.0),
                ParameterSpec(
                    "dc_exclusion_radius",
                    "DC exclusion radius",
                    PortType.FLOAT,
                    0.0,
                    minimum=0.0,
                    maximum=1.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "band_aggregation",
                    "Band aggregation",
                    PortType.STRING,
                    BAND_MEAN,
                    choices=BAND_AGGREGATIONS,
                ),
                ParameterSpec(
                    "percentile",
                    "Percentile",
                    PortType.FLOAT,
                    90.0,
                    minimum=0.0,
                    maximum=100.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "activation_threshold",
                    "Activation threshold",
                    PortType.FLOAT,
                    0.0,
                    minimum=0.0,
                    maximum=1.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
            ),
            ExecutionKind.STATEFUL,
            FourierRuntime,
            aliases=("spatial spectrum", "frequency notes", "fft to pitch"),
            parameter_validator=_validate_parameters,
            parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
        ),
    )


def _window_2d(shape: tuple[int, int], window: str) -> NDArray[np.float64]:
    height, width = shape
    if window == NONE:
        return np.ones(shape, dtype=np.float64)
    vertical = np.hanning(height) if window == HANN else np.hamming(height)
    horizontal = np.hanning(width) if window == HANN else np.hamming(width)
    return np.multiply.outer(vertical, horizontal)


def _amplitude_strength(
    amplitude: float,
    floor: float,
    ceiling: float,
    threshold: float,
) -> float | None:
    normalized = min(1.0, max(0.0, (amplitude - floor) / (ceiling - floor)))
    if threshold == 1.0:
        return 1.0 if normalized == 1.0 else None
    if normalized <= threshold:
        return None
    return (normalized - threshold) / (1.0 - threshold)


def _validate_algorithm_parameters(
    *,
    window: str,
    frequency_mapping: str,
    frequency_minimum: float,
    frequency_maximum: float,
    amplitude_floor: float,
    amplitude_ceiling: float,
    dc_exclusion_radius: float,
    band_aggregation: str,
    percentile: float,
    activation_threshold: float,
) -> None:
    _validate_window(window)
    _validate_frequency_parameters(
        frequency_mapping, frequency_minimum, frequency_maximum, dc_exclusion_radius
    )
    if (
        not math.isfinite(amplitude_floor)
        or not math.isfinite(amplitude_ceiling)
        or amplitude_ceiling <= amplitude_floor
    ):
        raise ValueError("Fourier amplitude ceiling must be finite and greater than floor")
    _validate_aggregation(band_aggregation, percentile)
    if not math.isfinite(activation_threshold) or not 0.0 <= activation_threshold <= 1.0:
        raise ValueError("Fourier activation threshold must be finite and in the range 0..1")


def _validate_window(window: str) -> None:
    if window not in WINDOWS:
        raise ValueError(f"Unknown Fourier window: {window!r}")


def _validate_frequency_parameters(
    mapping: str,
    minimum: float,
    maximum: float,
    dc_radius: float,
) -> None:
    if mapping not in FREQUENCY_MAPPINGS:
        raise ValueError(f"Unknown Fourier frequency mapping: {mapping!r}")
    if (
        not math.isfinite(minimum)
        or not math.isfinite(maximum)
        or not 0.0 <= minimum < maximum <= 1.0
    ):
        raise ValueError("Fourier frequency range must satisfy 0 <= minimum < maximum <= 1")
    if not math.isfinite(dc_radius) or not 0.0 <= dc_radius <= 1.0:
        raise ValueError("Fourier DC exclusion radius must be finite and in the range 0..1")


def _validate_aggregation(aggregation: str, percentile: float) -> None:
    if aggregation not in BAND_AGGREGATIONS:
        raise ValueError(f"Unknown Fourier band aggregation: {aggregation!r}")
    if not math.isfinite(percentile) or not 0.0 <= percentile <= 100.0:
        raise ValueError("Fourier percentile must be finite and in the range 0..100")


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    try:
        _validate_algorithm_parameters(
            window=_text(parameters["window"]),
            frequency_mapping=_text(parameters["frequency_mapping"]),
            frequency_minimum=_number(parameters["frequency_minimum"]),
            frequency_maximum=_number(parameters["frequency_maximum"]),
            amplitude_floor=_number(parameters["amplitude_floor"]),
            amplitude_ceiling=_number(parameters["amplitude_ceiling"]),
            dc_exclusion_radius=_number(parameters["dc_exclusion_radius"]),
            band_aggregation=_text(parameters["band_aggregation"]),
            percentile=_number(parameters["percentile"]),
            activation_threshold=_number(parameters["activation_threshold"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
    return errors


def _channel(value: object) -> ChannelFrame:
    if isinstance(value, ChannelFrame):
        return value
    raise TypeError(f"Expected ChannelFrame, got {type(value).__name__}")


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected numeric value, got {type(value).__name__}")


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean value, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string value, got {type(value).__name__}")


__all__ = [
    "BAND_MEAN",
    "BAND_PERCENTILE",
    "FOURIER_TYPE_ID",
    "HAMMING",
    "HANN",
    "HORIZONTAL",
    "NONE",
    "RADIAL_MAGNITUDE",
    "VERTICAL",
    "FourierBandMapKey",
    "FourierRuntime",
    "FourierShapeCache",
    "build_fourier_band_map",
    "create_fourier_definitions",
    "fourier_band_amplitudes",
    "fourier_to_midi_state",
]
