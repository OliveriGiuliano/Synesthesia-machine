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
    values, errors = create_fourier_definitions()[0].parameter_values(
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
    assert runtime.cache.misses == 1
    assert runtime.cache.hits == 0
    first_key = runtime.cache.key

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
    assert runtime.cache.key == first_key
    assert runtime.cache.misses == 1
    assert runtime.cache.hits == 1


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
    first_key = runtime.cache.key

    _runtime_state(runtime, changed_channel, **overrides)

    assert runtime.cache.key != first_key
    assert runtime.cache.misses == 2
    assert runtime.cache.hits == 0


@pytest.mark.parametrize("reason", list(ResetReason))
def test_every_lifecycle_reset_and_close_release_the_shape_cache(reason: ResetReason) -> None:
    runtime = FourierRuntime(NODE)
    _runtime_state(runtime, _sinusoid(horizontal_cycles=4))
    assert runtime.cache.has_entry
    runtime.reset(reason)
    assert not runtime.cache.has_entry
    assert runtime.cache.key is None
    _runtime_state(runtime, _sinusoid(horizontal_cycles=4))
    runtime.close()
    assert not runtime.cache.has_entry


def test_runtime_clock_error_is_recoverable_without_populating_cache() -> None:
    runtime = FourierRuntime(NODE)
    with pytest.raises(ExpectedNodeError, match="clock does not match") as captured:
        runtime.process(
            {"value": _channel(np.ones((8, 8), dtype=np.float32), clock=OTHER_CLOCK)},
            _parameters(),
            _context(),
        )
    assert captured.value.code == "invalid_fourier"
    assert not runtime.cache.has_entry


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
                    definition,
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
    assert definition.type_id == FOURIER_TYPE_ID
    assert definition.execution_kind is ExecutionKind.STATEFUL
    assert tuple(port.id for port in definition.inputs) == ("value",)
    assert definition.parameter_groups[0].id == "musical"
    registry = create_application_registry()
    assert len(registry.definitions()) >= 57
    assert registry.require(FOURIER_TYPE_ID).type_id == FOURIER_TYPE_ID

    values = np.array([[np.nan, np.inf], [-np.inf, 1.0]], dtype=np.float32)
    output = _runtime_state(FourierRuntime(NODE), _channel(values))
    assert isinstance(output, MidiStateFrame)

    for overrides, expected in (
        ({"frequency_minimum": 1.0, "frequency_maximum": 1.0}, "frequency range"),
        ({"amplitude_floor": 1.0, "amplitude_ceiling": 1.0}, "amplitude ceiling"),
    ):
        _, errors = definition.parameter_values(overrides)
        assert any(expected in error for error in errors)
