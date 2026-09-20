"""Synthetic spatial-frequency and cache tests for the Fourier node."""

from __future__ import annotations

from collections.abc import Mapping
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
    ParameterValue,
    PortType,
    read_only_float32,
)
from synesthesia_machine.midi import resolve_musical_selector
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, ResetReason
from synesthesia_machine.nodes.synesthesia import (
    BAND_MEAN,
    BAND_PERCENTILE,
    FOURIER_TYPE_ID,
    HAMMING,
    HANN,
    HORIZONTAL,
    NONE,
    RADIAL_MAGNITUDE,
    VERTICAL,
    FourierBandMapKey,
    FourierRuntime,
    FourierShapeCache,
    build_fourier_band_map,
    create_fourier_definitions,
    fourier_band_amplitudes,
    fourier_to_midi_state,
)
from synesthesia_machine.nodes.synesthesia.musical import CommonMusicalSettings
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000650")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000000651")
NODE = UUID("00000000-0000-0000-0000-000000000652")
SOURCE = UUID("00000000-0000-0000-0000-000000000653")
DOCUMENT = UUID("00000000-0000-0000-0000-000000000654")


def _context(clock: UUID = CLOCK, tick: int = 1) -> FrameContext:
    return FrameContext(clock, tick, tick - 1, 0.0, tick, None, False)


def _channel(values: NDArray[np.float32], *, clock: UUID = CLOCK) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(values, dtype=np.float32)),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        _context(clock),
    )


def _sinusoid(
    *, width: int = 32, height: int = 32, horizontal_cycles: int = 0, vertical_cycles: int = 0
) -> ChannelFrame:
    y, x = np.indices((height, width), dtype=np.float64)
    phase = 2.0 * np.pi * (horizontal_cycles * x / width + vertical_cycles * y / height)
    values = np.asarray(0.5 + 0.5 * np.cos(phase), dtype=np.float32)
    return _channel(values)


def _settings(minimum: int = 60, maximum: int = 63) -> CommonMusicalSettings:
    return CommonMusicalSettings(
        resolve_musical_selector("C", "chromatic", minimum, maximum),
        0,
        16,
        1,
        127,
    )


def _parameters(**overrides: object) -> Mapping[str, ParameterValue]:
    values, errors = create_fourier_definitions()[0].execution.parameter_values(
        {
            "midi_minimum": 60,
            "midi_maximum": 63,
            "window": NONE,
            "frequency_mapping": HORIZONTAL,
            "band_aggregation": BAND_PERCENTILE,
            "percentile": 100.0,
            "amplitude_floor": 0.0,
            "amplitude_ceiling": 6.0,
            **overrides,
        }
    )
    assert not errors
    return values


def _runtime_state(
    runtime: FourierRuntime,
    channel: ChannelFrame,
    **overrides: object,
) -> MidiStateFrame:
    output = runtime.process({"value": channel}, _parameters(**overrides), channel.context)["midi"]
    assert isinstance(output, MidiStateFrame)
    return output


def test_known_horizontal_and_vertical_sinusoids_map_to_expected_notes() -> None:
    horizontal = fourier_to_midi_state(
        _sinusoid(horizontal_cycles=4),
        window=NONE,
        subtract_mean=True,
        frequency_mapping=HORIZONTAL,
        frequency_minimum=0.0,
        frequency_maximum=1.0,
        amplitude_floor=0.0,
        amplitude_ceiling=6.0,
        dc_exclusion_radius=0.0,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
        activation_threshold=0.01,
        settings=_settings(),
        node_id=NODE,
        context=_context(),
    )
    vertical = fourier_to_midi_state(
        _sinusoid(vertical_cycles=8),
        window=NONE,
        subtract_mean=True,
        frequency_mapping=VERTICAL,
        frequency_minimum=0.0,
        frequency_maximum=1.0,
        amplitude_floor=0.0,
        amplitude_ceiling=6.0,
        dc_exclusion_radius=0.0,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
        activation_threshold=0.01,
        settings=_settings(),
        node_id=NODE,
        context=_context(),
    )
    assert tuple(key.note for key in horizontal.notes) == (61,)
    assert tuple(key.note for key in vertical.notes) == (62,)


def test_band_map_has_fixed_normalized_units_and_dc_exclusion() -> None:
    horizontal = build_fourier_band_map(FourierBandMapKey((8, 8), 4, HORIZONTAL, 0.0, 1.0, 0.0))
    radial = build_fourier_band_map(FourierBandMapKey((8, 8), 4, RADIAL_MAGNITUDE, 0.0, 1.0, 0.2))
    assert horizontal[0, 0] == 0
    assert horizontal[0, 1] == 1
    assert horizontal[0, 2] == 2
    assert horizontal[0, 4] == 3
    assert radial[0, 0] == -1
    assert radial[0, 1] == -1
    assert radial[0, 2] >= 0
    assert horizontal.dtype == np.int16
    assert horizontal.flags.c_contiguous
    assert not horizontal.flags.writeable


@pytest.mark.parametrize("window", [NONE, HANN, HAMMING])
def test_windows_and_aggregations_produce_finite_deterministic_amplitudes(window: str) -> None:
    channel = _sinusoid(horizontal_cycles=4)
    band_map = build_fourier_band_map(
        FourierBandMapKey(channel.data.shape, 4, HORIZONTAL, 0.0, 1.0, 0.0)
    )
    mean = fourier_band_amplitudes(
        channel,
        band_indexes=band_map,
        note_count=4,
        window=window,
        subtract_mean=True,
        band_aggregation=BAND_MEAN,
        percentile=90.0,
    )
    maximum = fourier_band_amplitudes(
        channel,
        band_indexes=band_map,
        note_count=4,
        window=window,
        subtract_mean=True,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
    )
    assert np.all(np.isfinite(mean))
    assert np.all(np.isfinite(maximum))
    assert maximum[1] >= mean[1] > 0.0


def test_explicit_amplitude_range_and_common_musical_rules() -> None:
    channel = _sinusoid(horizontal_cycles=10)
    allowed = resolve_musical_selector("D", "major", 60, 72)
    settings = CommonMusicalSettings(allowed, 4, 1, 10, 110)
    band_map = build_fourier_band_map(
        FourierBandMapKey(channel.data.shape, len(allowed.allowed_notes), HORIZONTAL, 0.0, 1.0, 0.0)
    )
    amplitudes = fourier_band_amplitudes(
        channel,
        band_indexes=band_map,
        note_count=len(allowed.allowed_notes),
        window=NONE,
        subtract_mean=True,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
    )
    state = fourier_to_midi_state(
        channel,
        window=NONE,
        subtract_mean=True,
        frequency_mapping=HORIZONTAL,
        frequency_minimum=0.0,
        frequency_maximum=1.0,
        amplitude_floor=0.0,
        amplitude_ceiling=float(amplitudes[4]),
        dc_exclusion_radius=0.0,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
        activation_threshold=0.01,
        settings=settings,
        node_id=NODE,
        context=_context(),
    )
    assert state.notes == {MidiNoteKey(4, 67): 110}


def test_shape_cache_reuses_for_every_non_map_setting() -> None:
    runtime = FourierRuntime(NODE)
    channel = _sinusoid(horizontal_cycles=4)
    _runtime_state(runtime, channel)
    assert runtime.shape_cache.misses == 1
    assert runtime.shape_cache.hits == 0
    first_key = runtime.shape_cache.key

    _runtime_state(
        runtime,
        channel,
        window=HANN,
        subtract_mean=False,
        amplitude_floor=-1.0,
        amplitude_ceiling=8.0,
        band_aggregation=BAND_MEAN,
        percentile=25.0,
        activation_threshold=0.2,
        midi_minimum=61,
        midi_maximum=64,
        midi_channel=5,
        maximum_polyphony=1,
        minimum_velocity=10,
        maximum_velocity=110,
    )
    assert runtime.shape_cache.key == first_key
    assert runtime.shape_cache.misses == 1
    assert runtime.shape_cache.hits == 1


@pytest.mark.parametrize(
    ("changed_channel", "overrides"),
    [
        (_sinusoid(width=40, height=32, horizontal_cycles=5), {}),
        (_sinusoid(horizontal_cycles=4), {"midi_maximum": 64}),
        (_sinusoid(horizontal_cycles=4), {"frequency_mapping": VERTICAL}),
        (_sinusoid(horizontal_cycles=4), {"frequency_minimum": 0.1}),
        (_sinusoid(horizontal_cycles=4), {"frequency_maximum": 0.8}),
        (_sinusoid(horizontal_cycles=4), {"dc_exclusion_radius": 0.2}),
    ],
    ids=("shape", "note-count", "mapping", "minimum", "maximum", "dc-radius"),
)
def test_shape_cache_invalidates_for_each_map_key_field(
    changed_channel: ChannelFrame,
    overrides: Mapping[str, object],
) -> None:
    runtime = FourierRuntime(NODE)
    _runtime_state(runtime, _sinusoid(horizontal_cycles=4))
    first_key = runtime.shape_cache.key

    _runtime_state(runtime, changed_channel, **overrides)

    assert runtime.shape_cache.key != first_key
    assert runtime.shape_cache.misses == 2
    assert runtime.shape_cache.hits == 0


@pytest.mark.parametrize("reason", list(ResetReason))
def test_every_lifecycle_reset_and_close_release_the_shape_cache(reason: ResetReason) -> None:
    runtime = FourierRuntime(NODE)
    _runtime_state(runtime, _sinusoid(horizontal_cycles=4))
    assert runtime.shape_cache.has_entry
    runtime.reset(reason)
    assert not runtime.shape_cache.has_entry
    assert runtime.shape_cache.key is None
    _runtime_state(runtime, _sinusoid(horizontal_cycles=4))
    runtime.close()
    assert not runtime.shape_cache.has_entry


def test_runtime_clock_error_is_recoverable_without_populating_cache() -> None:
    runtime = FourierRuntime(NODE)
    with pytest.raises(ExpectedNodeError, match="clock does not match") as captured:
        runtime.process(
            {"value": _channel(np.ones((8, 8), dtype=np.float32), clock=OTHER_CLOCK)},
            _parameters(),
            _context(),
        )
    assert captured.value.code == "invalid_fourier"
    assert not runtime.shape_cache.has_entry


def test_scheduler_no_data_suppresses_fourier_invocation() -> None:
    source = PortKey(SOURCE, "value")
    definition = create_fourier_definitions()[0]
    scheduler = Scheduler(
        ExecutionPlan(
            DOCUMENT,
            1,
            (
                CompiledNode(
                    NODE,
                    definition.execution,
                    parameters=_parameters(),
                    input_bindings={"value": InputBinding(source)},
                    input_types={"value": PortType.CHANNEL},
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


def test_definition_registry_nonfinite_and_range_validation_contracts() -> None:
    definition = create_fourier_definitions()[0]
    assert definition.execution.type_id == FOURIER_TYPE_ID
    assert definition.execution.execution_kind is ExecutionKind.STATEFUL
    assert tuple(port.id for port in definition.execution.inputs) == ("value",)
    assert definition.presentation.parameter_groups[0].id == "musical"
    registry = create_application_registry()
    assert len(registry.definitions()) >= 57
    assert registry.require(FOURIER_TYPE_ID).execution.type_id == FOURIER_TYPE_ID

    values = np.array([[np.nan, np.inf], [-np.inf, 1.0]], dtype=np.float32)
    output = _runtime_state(FourierRuntime(NODE), _channel(values))
    assert isinstance(output, MidiStateFrame)

    for overrides, expected in (
        ({"frequency_minimum": 1.0, "frequency_maximum": 1.0}, "frequency range"),
        ({"amplitude_floor": 1.0, "amplitude_ceiling": 1.0}, "amplitude ceiling"),
    ):
        _, errors = definition.execution.parameter_values(overrides)
        assert any(expected in error for error in errors)


def test_band_amplitudes_match_per_band_gather_reference() -> None:
    # The vectorized group-sort rewrite must stay bit-identical to the
    # historical per-band boolean gather for every aggregation mode, window,
    # and the DC-excluded (map value -1) cells.
    rng = np.random.default_rng(7)
    data = read_only_float32(rng.uniform(-1.0, 1.0, size=(17, 23)).astype(np.float32))
    channel = _channel(data)
    band_indexes = build_fourier_band_map(
        FourierBandMapKey((17, 23), 6, HORIZONTAL, 0.05, 0.95, 0.02)
    )
    for window in (NONE, HANN, HAMMING):
        for aggregation in (BAND_MEAN, BAND_PERCENTILE):
            for subtract_mean in (False, True):
                got = fourier_band_amplitudes(
                    channel,
                    band_indexes=band_indexes,
                    note_count=6,
                    window=window,
                    subtract_mean=subtract_mean,
                    band_aggregation=aggregation,
                    percentile=40.0,
                )
                normalized = (data - channel.nominal_min) / (
                    channel.nominal_max - channel.nominal_min
                )
                samples = np.asarray(
                    np.clip(np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0),
                    dtype=np.float64,
                )
                if subtract_mean:
                    samples = samples - float(np.mean(samples, dtype=np.float64))
                from synesthesia_machine.nodes.synesthesia.fourier import _window_2d

                samples = samples * _window_2d(samples.shape, window)
                log_magnitude = np.log1p(np.abs(np.fft.fft2(samples)))
                reference = np.zeros(6, dtype=np.float64)
                for band in range(6):
                    selected = log_magnitude[band_indexes == band]
                    if selected.size == 0:
                        continue
                    reference[band] = (
                        float(np.mean(selected, dtype=np.float64))
                        if aggregation == BAND_MEAN
                        else float(np.percentile(selected, 40.0))
                    )
                assert np.array_equal(got, reference), (window, aggregation, subtract_mean)


def test_window_arrays_are_cached_and_frozen() -> None:
    from synesthesia_machine.nodes.synesthesia.fourier import _window_2d

    first = _window_2d((13, 29), HANN)
    second = _window_2d((13, 29), HANN)
    assert first is second
    assert not first.flags.writeable
    assert not second.flags.writeable
    unit = _window_2d((13, 29), NONE)
    assert unit.shape == (13, 29)
    assert not unit.flags.writeable


def test_analysis_max_dimension_zero_keeps_full_resolution_amplitudes() -> None:
    import cv2  # noqa: F401  (reference tests below use cv2.resize)

    channel = _sinusoid(horizontal_cycles=1)
    full = fourier_band_amplitudes(
        channel,
        band_indexes=build_fourier_band_map(
            FourierBandMapKey(
                channel.data.shape,
                4,
                HORIZONTAL,
                0.0,
                1.0,
                0.0,
            )
        ),
        note_count=4,
        window=NONE,
        subtract_mean=False,
        band_aggregation=BAND_MEAN,
        percentile=100.0,
    )
    zero = fourier_band_amplitudes(
        channel,
        band_indexes=build_fourier_band_map(
            FourierBandMapKey(
                channel.data.shape,
                4,
                HORIZONTAL,
                0.0,
                1.0,
                0.0,
            )
        ),
        note_count=4,
        window=NONE,
        subtract_mean=False,
        band_aggregation=BAND_MEAN,
        percentile=100.0,
    )
    assert np.array_equal(zero, full)


def test_analysis_max_dimension_matches_pre_reduced_full_resolution_reference() -> None:
    import cv2

    from synesthesia_machine.contracts import read_only_float32
    from synesthesia_machine.nodes.synesthesia.fourier import fourier_to_midi_state

    channel = _sinusoid(horizontal_cycles=1, vertical_cycles=1)
    # 32x32 reduced to a 16x16 analysis frame (longest side 32 > 16, exact half).
    reduced_state = fourier_to_midi_state(
        channel,
        window=NONE,
        subtract_mean=False,
        frequency_mapping=HORIZONTAL,
        frequency_minimum=0.0,
        frequency_maximum=1.0,
        amplitude_floor=0.0,
        amplitude_ceiling=6.0,
        dc_exclusion_radius=0.0,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
        activation_threshold=0.0,
        settings=_settings(),
        node_id=NODE,
        context=channel.context,
        cache=FourierShapeCache(),
        analysis_max_dimension=16,
    )
    reduced_data = read_only_float32(
        np.asarray(
            cv2.resize(
                np.asarray(channel.data, dtype=np.float32),
                (16, 16),
                interpolation=cv2.INTER_LINEAR,
            ),
            dtype=np.float32,
        )
    )
    pre_reduced = ChannelFrame(
        reduced_data,
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )
    reference = fourier_to_midi_state(
        pre_reduced,
        window=NONE,
        subtract_mean=False,
        frequency_mapping=HORIZONTAL,
        frequency_minimum=0.0,
        frequency_maximum=1.0,
        amplitude_floor=0.0,
        amplitude_ceiling=6.0,
        dc_exclusion_radius=0.0,
        band_aggregation=BAND_PERCENTILE,
        percentile=100.0,
        activation_threshold=0.0,
        settings=_settings(),
        node_id=NODE,
        context=channel.context,
        cache=FourierShapeCache(),
    )
    assert reduced_state.notes == reference.notes


def test_analysis_max_dimension_covers_caps_and_rejects_negative_values() -> None:
    from synesthesia_machine.nodes.synesthesia.fourier import fourier_to_midi_state

    channel = _sinusoid(horizontal_cycles=1)

    def run(cap: int):
        return fourier_to_midi_state(
            channel,
            window=NONE,
            subtract_mean=False,
            frequency_mapping=HORIZONTAL,
            frequency_minimum=0.0,
            frequency_maximum=1.0,
            amplitude_floor=0.0,
            amplitude_ceiling=6.0,
            dc_exclusion_radius=0.0,
            band_aggregation=BAND_PERCENTILE,
            percentile=100.0,
            activation_threshold=0.0,
            settings=_settings(),
            node_id=NODE,
            context=channel.context,
            cache=FourierShapeCache(),
            analysis_max_dimension=cap,
        )

    # Cap at or above the frame keeps the full-resolution result bit-identical.
    assert run(0).notes == run(1024).notes
    with pytest.raises(ValueError, match="non-negative"):
        run(-1)
    _, errors = create_fourier_definitions()[0].execution.parameter_values(
        {"analysis_max_dimension": -1}
    )
    assert "analysis_max_dimension" in " ".join(errors)
