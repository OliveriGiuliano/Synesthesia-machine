"""Callback-safe debug synth, Generate Audio sink, and global panic tests."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from uuid import UUID

import numpy as np
import pytest
from numpy.typing import NDArray
from tests.support.graph_factories import frame_context, make_definition

import synesthesia_machine.midi.debug_synth as debug_synth_module
from synesthesia_machine.contracts import (
    DeviceDescriptor,
    DeviceKind,
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.midi import (
    DebugSynth,
    NullDebugSynth,
    SynthConfiguration,
    SynthWaveform,
)
from synesthesia_machine.midi.debug_synth import (
    AudioCallback,
    enumerate_audio_output_devices,
    open_sounddevice_stream,
)
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    NodeDefinition,
    ResetReason,
)
from synesthesia_machine.nodes.output import (
    GENERATE_AUDIO_TYPE_ID,
    GenerateAudioRuntime,
    create_output_definitions,
)
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import EngineFacade, InProcessEngineClient, PortKey, Scheduler

CLOCK_ID = UUID("00000000-0000-0000-0000-000000000401")
MIDI_SOURCE_ID = UUID("00000000-0000-0000-0000-000000000402")
AUDIO_NODE_ID = UUID("00000000-0000-0000-0000-000000000403")
PANIC_NODE_ID = UUID("00000000-0000-0000-0000-000000000404")


def test_audio_catalogue_uses_stable_indexes_and_open_resolves_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        debug_synth_module.sd,
        "query_devices",
        lambda: (
            {"name": "  Studio\nSpeakers  ", "max_output_channels": 2},
            {"name": "Input only", "max_output_channels": 0},
        ),
    )
    assert enumerate_audio_output_devices() == (
        DeviceDescriptor(DeviceKind.AUDIO_OUTPUT, "", "System default audio output", True),
        DeviceDescriptor(DeviceKind.AUDIO_OUTPUT, "sounddevice:0", "Studio Speakers"),
    )

    arguments: dict[str, object] = {}

    def output_stream(**kwargs: object) -> MockAudioStream:
        arguments.update(kwargs)
        return MockAudioStream(lambda _out, _frames, _time_info, _status: None)

    monkeypatch.setattr(debug_synth_module.sd, "OutputStream", output_stream)

    def callback(
        _out: NDArray[np.float32],
        _frames: int,
        _time_info: object,
        _status: object,
    ) -> None:
        return

    open_sounddevice_stream(SynthConfiguration(output_device="sounddevice:0"), callback)
    assert arguments["device"] == 0

    open_sounddevice_stream(SynthConfiguration(output_device="Legacy device name"), callback)
    assert arguments["device"] == "Legacy device name"


class MockAudioStream:
    def __init__(self, callback: AudioCallback, *, fail_start: bool = False) -> None:
        self.callback = callback
        self.fail_start = fail_start
        self.start_count = 0
        self.abort_count = 0
        self.close_count = 0

    def start(self) -> object:
        self.start_count += 1
        if self.fail_start:
            raise OSError("synthetic PortAudio start failure")
        return self

    def abort(self, ignore_errors: bool = True) -> object:
        del ignore_errors
        self.abort_count += 1
        return self

    def close(self, ignore_errors: bool = True) -> object:
        del ignore_errors
        self.close_count += 1
        return self

    def render(self, frames: int) -> NDArray[np.float32]:
        outdata = np.full((frames, 2), np.nan, dtype=np.float32)
        self.callback(outdata, frames, object(), object())
        return outdata


class StreamFactory:
    fail_start: bool
    streams: list[MockAudioStream]

    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.streams = []

    def __call__(
        self, configuration: SynthConfiguration, callback: AudioCallback
    ) -> MockAudioStream:
        del configuration
        stream = MockAudioStream(callback, fail_start=self.fail_start)
        self.streams.append(stream)
        return stream


class RecordingSynth:
    configuration: SynthConfiguration
    updates: list[MidiStateFrame]
    panic_count: int
    panic_generations: list[int]
    close_count: int

    def __init__(self, configuration: SynthConfiguration) -> None:
        self.configuration = configuration
        self.updates = []
        self.panic_count = 0
        self.panic_generations = []
        self.close_count = 0

    def update(self, state: MidiStateFrame) -> None:
        self.updates.append(state)

    def panic(self, publish_generation: int = 0) -> None:
        self.panic_count += 1
        self.panic_generations.append(publish_generation)

    def close(self) -> None:
        self.close_count += 1


class RecordingSynthFactory:
    synths: list[RecordingSynth]

    def __init__(self) -> None:
        self.synths = []

    def __call__(self, configuration: SynthConfiguration) -> RecordingSynth:
        synth = RecordingSynth(configuration)
        self.synths.append(synth)
        return synth


def _midi_state(
    notes: Mapping[tuple[int, int], int],
    *,
    tick_index: int = 1,
    publish_generation: int = 0,
) -> MidiStateFrame:
    context = frame_context(
        clock_id=CLOCK_ID, tick_index=tick_index, publish_generation=publish_generation
    )
    return MidiStateFrame(
        {MidiNoteKey(channel, note): velocity for (channel, note), velocity in notes.items()},
        context,
        MIDI_SOURCE_ID,
    )


def _parameters(
    definition: NodeDefinition, overrides: Mapping[str, object] | None = None
) -> dict[str, ParameterValue]:
    parameters, errors = definition.parameter_values(overrides or {})
    assert errors == []
    return parameters


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timed out waiting for background audio work")
        time.sleep(0.005)


def _source_audio_scheduler(
    synth_factory: RecordingSynthFactory,
) -> tuple[Scheduler, UUID, UUID]:
    source_definition = make_definition(
        "test.midi_source",
        output_type=PortType.MIDI_STATE,
        execution_kind=ExecutionKind.SOURCE,
    )
    audio_definition = create_output_definitions(synth_factory=synth_factory)[0]
    registry = NodeRegistry((source_definition, audio_definition))
    document = GraphDocument()
    source_id = document.add_node(source_definition.type_id, node_id=MIDI_SOURCE_ID)
    audio_id = document.add_node(
        audio_definition.type_id,
        node_id=AUDIO_NODE_ID,
        parameters={"enabled": True},
    )
    document.add_connection(source_id, "value", audio_id, "midi")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    return Scheduler(plan), source_id, audio_id


def test_generate_audio_definition_is_opt_in_sink_and_demands_its_branch() -> None:
    synth_factory = RecordingSynthFactory()
    source_definition = make_definition(
        "test.default_audio_source",
        output_type=PortType.MIDI_STATE,
        execution_kind=ExecutionKind.SOURCE,
    )
    definition = create_output_definitions(synth_factory=synth_factory)[0]
    parameters = _parameters(definition)
    assert definition.type_id == GENERATE_AUDIO_TYPE_ID
    assert definition.execution_kind is ExecutionKind.SINK
    assert definition.cache_policy is CachePolicy.NEVER
    assert definition.handles_no_data
    assert parameters == {
        "enabled": False,
        "waveform": "SINE",
        "volume": 0.15,
        "attack_ms": 10.0,
        "release_ms": 80.0,
        "max_voices": 32,
        "output_device": "",
    }

    registry = NodeRegistry((source_definition, definition))
    document = GraphDocument()
    source_id = document.add_node(source_definition.type_id)
    audio_id = document.add_node(definition.type_id)
    document.add_connection(source_id, "value", audio_id, "midi")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    assert plan.demand_roots == frozenset({audio_id})
    source = plan.node(source_id)
    assert source is not None and source.is_demanded

    scheduler = Scheduler(plan)
    result = scheduler.execute_tick(
        frame_context(clock_id=source_id),
        source_values={PortKey(source_id, "value"): _midi_state({(0, 60): 100})},
    )
    assert result.invocation_counts == {audio_id: 1}
    assert synth_factory.synths == []
    scheduler.close()


def test_runtime_opens_lazily_updates_state_and_replaces_changed_configuration() -> None:
    synth_factory = RecordingSynthFactory()
    definition = create_output_definitions(synth_factory=synth_factory)[0]
    runtime = definition.runtime_factory(AUDIO_NODE_ID)
    midi = _midi_state({(2, 69): 96})
    disabled = _parameters(definition)
    runtime.process({"midi": midi}, disabled, midi.context)
    assert synth_factory.synths == []

    enabled = _parameters(
        definition,
        {
            "enabled": True,
            "waveform": "TRIANGLE",
            "volume": 0.25,
            "attack_ms": 4.0,
            "release_ms": 25.0,
            "max_voices": 12,
            "output_device": "Named test device",
        },
    )
    runtime.process({"midi": midi}, enabled, midi.context)
    assert isinstance(runtime, GenerateAudioRuntime)
    assert runtime.wait_until_idle()
    runtime.process({"midi": midi}, enabled, midi.context)
    assert runtime.wait_until_idle()
    assert len(synth_factory.synths) == 1
    first = synth_factory.synths[0]
    assert first.updates == [midi, midi]
    assert first.configuration == SynthConfiguration(
        waveform=SynthWaveform.TRIANGLE,
        volume=0.25,
        attack_ms=4.0,
        release_ms=25.0,
        max_voices=12,
        output_device="Named test device",
    )

    changed = dict(enabled)
    changed["volume"] = 0.4
    runtime.process({"midi": midi}, changed, midi.context)
    assert runtime.wait_until_idle()
    assert first.close_count == 1
    assert len(synth_factory.synths) == 2
    assert synth_factory.synths[1].configuration.volume == 0.4

    runtime.process({"midi": midi}, disabled, midi.context)
    assert runtime.wait_until_idle()
    assert synth_factory.synths[1].close_count == 1
    runtime.close()


def test_scheduler_no_data_reset_panic_and_close_cannot_leave_stale_state() -> None:
    synth_factory = RecordingSynthFactory()
    scheduler, source_id, audio_id = _source_audio_scheduler(synth_factory)
    midi = _midi_state({(0, 60): 100})
    first = scheduler.execute_tick(
        midi.context,
        source_values={PortKey(source_id, "value"): midi},
    )
    assert first.invocation_counts == {audio_id: 1}
    _wait_for(lambda: bool(synth_factory.synths))
    synth = synth_factory.synths[0]
    assert synth.updates == [midi]

    missing = scheduler.execute_tick(
        frame_context(clock_id=source_id, tick_index=2),
        source_values={PortKey(source_id, "value"): NoData},
    )
    assert missing.invocation_counts == {audio_id: 1}
    assert synth.panic_count == 1

    scheduler.reset_source(source_id, ResetReason.SOURCE_RESTARTED)
    assert synth.panic_count == 2
    scheduler.panic()
    assert synth.panic_count == 3
    scheduler.close()
    assert synth.close_count == 1


def test_runtime_no_data_panics_with_the_ticks_publish_generation_latched() -> None:
    # ADR-0022: a missing input must panic the synth with the publish
    # generation of the tick that lost the race, so a late in-flight state
    # from that tick cannot re-arm the silenced voices. As in the Send-MIDI
    # sink, a continuous absence panics once, on its first missing tick,
    # not on every tick.
    synth_factory = RecordingSynthFactory()
    definition = create_output_definitions(synth_factory=synth_factory)[0]
    runtime = definition.runtime_factory(AUDIO_NODE_ID)
    enabled = _parameters(definition, {"enabled": True})
    first = _midi_state({(0, 60): 100}, tick_index=1, publish_generation=1)
    runtime.process({"midi": first}, enabled, first.context)
    assert runtime.wait_until_idle()
    assert synth_factory.synths, "the first publish opens the synth lazily"
    synth = synth_factory.synths[0]
    _wait_for(lambda: len(synth.updates) == 1)

    missing_two = frame_context(clock_id=MIDI_SOURCE_ID, tick_index=2, publish_generation=2)
    runtime.process({"midi": NoData}, enabled, missing_two)
    runtime.process(
        {"midi": NoData},
        enabled,
        frame_context(clock_id=MIDI_SOURCE_ID, tick_index=3, publish_generation=3),
    )
    # One panic for the whole absence, carrying the first missing tick's
    # generation so late states from that tick are stale.
    assert synth.panic_generations == [2]

    fresh = _midi_state({(0, 64): 90}, tick_index=4, publish_generation=4)
    runtime.process({"midi": fresh}, enabled, fresh.context)
    assert runtime.wait_until_idle()
    assert synth.panic_generations == [2]
    assert synth.updates[-1] is fresh

    runtime.close()
    # Close silences through the no-watermark default; the service panics
    # immediately on the caller and again from the worker's shutdown path,
    # but neither path stamps a tick generation.
    assert synth.panic_generations[0] == 2
    assert all(generation == 0 for generation in synth.panic_generations[1:])


def test_facade_no_data_panics_audio_sink_with_the_ticks_generation() -> None:
    # End to end (ADR-0022): the facade stamps every tick, so a missing
    # source value must panic the audio sink with that tick's generation; a
    # late in-flight state from the same tick cannot re-arm the voices.
    synth_factory = RecordingSynthFactory()
    source_definition = make_definition(
        "test.midi_source",
        output_type=PortType.MIDI_STATE,
        execution_kind=ExecutionKind.SOURCE,
    )
    audio_definition = create_output_definitions(synth_factory=synth_factory)[0]
    registry = NodeRegistry((source_definition, audio_definition))
    document = GraphDocument()
    source_id = document.add_node(source_definition.type_id, node_id=MIDI_SOURCE_ID)
    audio_id = document.add_node(
        audio_definition.type_id, node_id=AUDIO_NODE_ID, parameters={"enabled": True}
    )
    document.add_connection(source_id, "value", audio_id, "midi")
    facade = EngineFacade(registry)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        facade.tick(
            frame_context(clock_id=MIDI_SOURCE_ID),
            source_values={
                PortKey(MIDI_SOURCE_ID, "value"): _midi_state({(0, 60): 100}, tick_index=1)
            },
        )
        _wait_for(lambda: bool(synth_factory.synths))
        synth = synth_factory.synths[0]
        _wait_for(lambda: len(synth.updates) == 1)

        facade.tick(
            frame_context(clock_id=MIDI_SOURCE_ID, tick_index=2),
            source_values={PortKey(MIDI_SOURCE_ID, "value"): NoData},
        )
        assert synth.panic_generations == [2]

        facade.tick(
            frame_context(clock_id=MIDI_SOURCE_ID, tick_index=3),
            source_values={PortKey(MIDI_SOURCE_ID, "value"): NoData},
        )
        assert synth.panic_generations == [2]
    finally:
        facade.close()
    # Close silences through the no-watermark default; the service panics
    # immediately on the caller and again from the worker's shutdown path,
    # but neither path stamps a tick generation.
    assert synth.panic_generations[0] == 2
    assert all(generation == 0 for generation in synth.panic_generations[1:])


def test_null_synth_keeps_the_same_ordering_watermark_as_the_real_synth() -> None:
    # The null stand-in must apply the same state-ordering policy as the
    # real synth rather than a private re-implementation: identical
    # explicit-generation sequences leave identical watermarks, and the
    # real sink's observable behaviour follows the policy's verdicts.
    configuration = SynthConfiguration(attack_ms=0.0, max_voices=2, sample_rate=8000, block_size=32)
    stream_factory = StreamFactory()
    real = DebugSynth(configuration, stream_factory=stream_factory)
    null = NullDebugSynth(configuration)
    stream = stream_factory.streams[0]
    try:
        stale = _state_at({(0, 69): 127}, publish_generation=1)
        real.update(stale)
        null.update(stale)
        stream.render(configuration.block_size)
        assert real.active_voice_count == 1

        real.panic(publish_generation=1)
        null.panic(publish_generation=1)
        stream.render(configuration.block_size)

        real.update(stale)
        null.update(stale)
        stream.render(configuration.block_size)
        # The late stale frame is rejected by the real sink...
        assert real.active_voice_count == 0

        unordered = _state_at({(0, 72): 100}, publish_generation=0)
        real.update(unordered)
        null.update(unordered)
        stream.render(configuration.block_size)
        # ...the unordered frame is admitted.
        assert real.active_keys == (72,)

        real.panic(publish_generation=3)
        null.panic(publish_generation=3)
        assert (
            null._ordering.stale_upto_generation  # pyright: ignore[reportPrivateUsage]
            == real._ordering.stale_upto_generation  # pyright: ignore[reportPrivateUsage]
            == 3
        )
    finally:
        real.close()


def test_audio_open_failure_becomes_recoverable_scheduler_error() -> None:
    attempted = threading.Event()
    attempt_count = 0

    def fail_factory(configuration: SynthConfiguration) -> RecordingSynth:
        nonlocal attempt_count
        del configuration
        attempt_count += 1
        attempted.set()
        raise OSError("no test output device")

    source_definition = make_definition(
        "test.failing_audio_source",
        output_type=PortType.MIDI_STATE,
        execution_kind=ExecutionKind.SOURCE,
    )
    audio_definition = create_output_definitions(synth_factory=fail_factory)[0]
    registry = NodeRegistry((source_definition, audio_definition))
    document = GraphDocument()
    source_id = document.add_node(source_definition.type_id)
    audio_id = document.add_node(audio_definition.type_id, parameters={"enabled": True})
    document.add_connection(source_id, "value", audio_id, "midi")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    midi = _midi_state({(0, 60): 100})

    scheduler = Scheduler(plan)
    first = scheduler.execute_tick(
        midi.context,
        source_values={PortKey(source_id, "value"): midi},
    )
    assert first.errors == ()
    assert attempted.wait(2.0)
    result = scheduler.execute_tick(
        frame_context(clock_id=source_id, tick_index=2),
        source_values={PortKey(source_id, "value"): midi},
    )

    assert len(result.errors) == 1
    assert result.errors[0].node_id == audio_id
    assert result.errors[0].code == "audio_output_unavailable"
    assert result.errors[0].recoverable
    assert attempt_count == 1
    scheduler.close()


def test_runtime_never_opens_audio_device_on_graph_worker_and_discards_stale_state() -> None:
    entered_factory = threading.Event()
    release_factory = threading.Event()
    synth = RecordingSynth(SynthConfiguration())

    def blocking_factory(configuration: SynthConfiguration) -> RecordingSynth:
        del configuration
        entered_factory.set()
        if not release_factory.wait(2.0):
            raise TimeoutError("test did not release blocked synth factory")
        return synth

    runtime = GenerateAudioRuntime(AUDIO_NODE_ID, synth_factory=blocking_factory)
    definition = create_output_definitions()[0]
    parameters = _parameters(definition, {"enabled": True})
    midi = _midi_state({(0, 60): 100})

    started = time.monotonic()
    runtime.process({"midi": midi}, parameters, midi.context)
    assert time.monotonic() - started < 0.25
    assert entered_factory.wait(1.0)

    runtime.panic(0)
    release_factory.set()
    assert runtime.wait_until_idle()
    assert synth.updates == []
    assert synth.panic_count >= 1
    runtime.close()


def test_debug_synth_attack_channel_identity_release_and_stereo_bounds() -> None:
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        volume=0.6,
        attack_ms=8.0,
        release_ms=8.0,
        max_voices=4,
        sample_rate=1000,
        block_size=8,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    stream = stream_factory.streams[0]
    assert stream.start_count == 1

    synth.update(_midi_state({(0, 60): 127, (1, 60): 64}))
    active = stream.render(configuration.block_size)
    assert synth.active_keys == (60, 188)
    assert synth.active_voice_count == 2
    assert np.any(active != 0.0)
    assert np.all(np.isfinite(active))
    assert np.array_equal(active[:, 0], active[:, 1])
    assert float(np.max(np.abs(active))) <= 0.95

    synth.update(_midi_state({}, tick_index=2))
    release = stream.render(configuration.block_size)
    assert np.any(release != 0.0)
    assert synth.active_voice_count == 0
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0

    synth.close()
    synth.close()
    assert stream.abort_count == 1
    assert stream.close_count == 1


def test_debug_synth_velocity_scaling_voice_cap_and_deterministic_replacement() -> None:
    low_streams = StreamFactory()
    high_streams = StreamFactory()
    configuration = SynthConfiguration(
        waveform=SynthWaveform.SQUARE,
        volume=0.5,
        attack_ms=0.0,
        release_ms=20.0,
        max_voices=2,
        sample_rate=8000,
        block_size=64,
    )
    low = DebugSynth(configuration, stream_factory=low_streams)
    high = DebugSynth(configuration, stream_factory=high_streams)
    low.update(_midi_state({(0, 69): 32}))
    high.update(_midi_state({(0, 69): 127}))
    low_block = low_streams.streams[0].render(configuration.block_size)
    high_block = high_streams.streams[0].render(configuration.block_size)
    assert float(np.max(np.abs(high_block))) > 3.5 * float(np.max(np.abs(low_block)))

    high.update(_midi_state({(0, 60): 10, (0, 61): 100, (0, 62): 100, (0, 63): 127}))
    high_streams.streams[0].render(configuration.block_size)
    assert high.active_keys == (61, 63)

    high.update(_midi_state({(2, 64): 120, (2, 65): 110}, tick_index=2))
    high_streams.streams[0].render(configuration.block_size)
    assert high.active_keys == (320, 321)
    low.close()
    high.close()


@pytest.mark.parametrize("waveform", list(SynthWaveform))
def test_each_debug_waveform_renders_finite_audio(waveform: SynthWaveform) -> None:
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        waveform=waveform,
        volume=0.25,
        attack_ms=0.0,
        max_voices=1,
        sample_rate=8000,
        block_size=32,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    synth.update(_midi_state({(0, 69): 100}))
    rendered = stream_factory.streams[0].render(configuration.block_size)
    assert np.any(rendered != 0.0)
    assert np.all(np.isfinite(rendered))
    synth.close()


def test_debug_synth_uses_equal_tempered_a4_at_440_hz() -> None:
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        waveform=SynthWaveform.SINE,
        volume=0.4,
        attack_ms=0.0,
        max_voices=1,
        sample_rate=8000,
        block_size=16,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    synth.update(_midi_state({(0, 69): 127}))

    rendered = stream_factory.streams[0].render(configuration.block_size)
    sample_indexes = np.arange(configuration.block_size, dtype=np.float64)
    expected = np.sin(sample_indexes * (2.0 * np.pi * 440.0 / configuration.sample_rate)) * 0.4
    assert np.allclose(rendered[:, 0], expected, atol=1e-6)
    synth.close()


def test_debug_synth_envelope_ramps_hit_exact_stage_boundaries() -> None:
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        waveform=SynthWaveform.SINE,
        volume=0.5,
        attack_ms=12.5,
        release_ms=12.5,
        max_voices=1,
        sample_rate=8000,
        block_size=64,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    stream = stream_factory.streams[0]
    # A4 (note 69) is exactly 440 Hz under equal temperament, so the expected
    # phase is drift-free.
    synth.update(_midi_state({(0, 69): 127}))

    # 12.5 ms at 8 kHz is 100 envelope samples, so every stage advances by
    # (k + 1) / 100 per sample (the first sample already carries one step),
    # with the carried level quantized to float32 at each block boundary.
    phase_step = 2.0 * math.pi * 440.0 / configuration.sample_rate
    step = 1.0 / 100.0
    carry = 0.0
    for block in range(4):
        rendered = stream.render(configuration.block_size)
        offsets = np.arange(configuration.block_size)
        levels = np.minimum(1.0, carry + (offsets + 1) * step)
        expected = np.sin((block * 64 + offsets) * phase_step) * levels * 0.5
        assert np.allclose(rendered[:, 0], expected, atol=1e-6)
        assert np.array_equal(rendered[:, 0], rendered[:, 1])
        carry = float(np.float32(levels[-1]))

    # The attack crossed full level inside the second block, so later
    # blocks sustain: the SUSTAIN stage holds the level for the entire block.
    assert int(synth._stages[0]) == int(synth._SUSTAIN)
    rendered = stream.render(configuration.block_size)
    expected = np.sin((4 * 64 + np.arange(64)) * phase_step) * 0.5
    assert np.allclose(rendered[:, 0], expected, atol=1e-6)
    assert np.array_equal(rendered[:, 0], rendered[:, 1])

    synth.update(_midi_state({}, tick_index=2))
    rendered = stream.render(configuration.block_size)
    assert synth.active_voice_count == 1
    levels = np.maximum(0.0, 1.0 - (np.arange(64) + 1) * step)
    expected = np.sin((5 * 64 + np.arange(64)) * phase_step) * levels * 0.5
    assert np.allclose(rendered[:, 0], expected, atol=1e-6)
    assert float(levels[-1]) == pytest.approx(0.36, abs=1e-9)

    # The release reaches exactly zero mid-block (the float32-carry level
    # 0.36 clears one more 1/100 step than exact math suggests): from that
    # sample on the voice is inactive and renders exactly zero, neither
    # early nor late.
    rendered = stream.render(configuration.block_size)
    levels = np.maximum(0.0, float(np.float32(0.36)) - (np.arange(64) + 1) * step)
    expected = np.sin((6 * 64 + np.arange(64)) * phase_step) * levels * 0.5
    assert np.allclose(rendered[:, 0], expected, atol=1e-6)
    assert float(levels[35]) > 0.0
    assert float(levels[36]) == 0.0
    assert np.all(rendered[36:, 0] == 0.0)
    assert synth.active_voice_count == 0
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0
    synth.close()


def test_debug_synth_update_caps_by_loudness_and_orders_by_key() -> None:
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        waveform=SynthWaveform.SINE,
        volume=0.5,
        attack_ms=0.0,
        release_ms=80.0,
        max_voices=2,
        sample_rate=8000,
        block_size=64,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    # Published out of key order with a velocity tie: the voice cap keeps
    # note 60 (loudest) and note 61 (ties break to the lower key), and both
    # new voices allocate in key order regardless of publication order.
    synth.update(_midi_state({(0, 62): 100, (0, 60): 127, (0, 61): 100}))
    stream_factory.streams[0].render(configuration.block_size)
    assert synth.active_keys == (60, 61)
    synth.close()


def test_debug_synth_panic_silences_next_callback_and_allows_future_state() -> None:
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        attack_ms=0.0,
        max_voices=2,
        sample_rate=8000,
        block_size=32,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    stream = stream_factory.streams[0]
    synth.update(_midi_state({(0, 69): 127}))
    assert np.any(stream.render(configuration.block_size) != 0.0)

    synth.panic(publish_generation=1)
    panicked = stream.render(configuration.block_size)
    assert np.count_nonzero(panicked) == 0
    assert synth.active_voice_count == 0
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0

    synth.update(_midi_state({(0, 72): 100}, tick_index=2, publish_generation=2))
    assert np.any(stream.render(configuration.block_size) != 0.0)
    assert synth.active_keys == (72,)
    synth.close()


def _state_at(notes: Mapping[tuple[int, int], int], *, publish_generation: int) -> MidiStateFrame:
    context = FrameContext(
        clock_id=CLOCK_ID,
        tick_index=1,
        source_frame_index=0,
        source_time_s=0.0,
        received_monotonic_ns=1,
        deadline_monotonic_ns=None,
        is_realtime=False,
        publish_generation=publish_generation,
    )
    return MidiStateFrame(
        {MidiNoteKey(channel, note): velocity for (channel, note), velocity in notes.items()},
        context,
        MIDI_SOURCE_ID,
    )


def test_debug_synth_panic_drops_stale_state_and_allows_fresh_state() -> None:
    # A state frame from a tick that lost the race against a panic (generated
    # before the panic, published after it) must not re-arm the voices the
    # panic silenced; a frame generated after the panic must sound. Ordering
    # is the tick's publish generation, never a wall-clock instant.
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        attack_ms=0.0,
        max_voices=2,
        sample_rate=8000,
        block_size=32,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    stream = stream_factory.streams[0]

    stale = _state_at({(0, 69): 127}, publish_generation=1)
    synth.update(stale)
    assert np.any(stream.render(configuration.block_size) != 0.0)

    # The panic observed publish generation 1, the generation of the tick
    # still executing when the panic landed.
    synth.panic(publish_generation=1)
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0

    # A late publish of the pre-panic frame is stale and must not re-arm.
    synth.update(stale)
    assert synth.active_voice_count == 0
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0

    # A frame generated after the panic is fresh and sounds.
    synth.update(_state_at({(0, 72): 100}, publish_generation=2))
    assert np.any(stream.render(configuration.block_size) != 0.0)
    assert synth.active_keys == (72,)
    synth.close()


def test_debug_synth_panic_does_not_reject_states_the_engine_never_ordered() -> None:
    # Generation-0 states (source-built or directly scheduled) carry no tick
    # ordering, so a generation-bearing panic must not start rejecting them:
    # a source that feeds MIDI state directly must keep sounding after a panic.
    stream_factory = StreamFactory()
    configuration = SynthConfiguration(
        attack_ms=0.0,
        max_voices=2,
        sample_rate=8000,
        block_size=32,
    )
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    stream = stream_factory.streams[0]

    synth.panic(publish_generation=1)
    synth.update(_state_at({(0, 69): 127}, publish_generation=0))
    # The block after the panic clears the voices; the following block
    # renders the un-ordered state that the panic must not have rejected.
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0
    assert np.any(stream.render(configuration.block_size) != 0.0)
    assert synth.active_keys == (69,)
    synth.close()


def test_debug_synth_start_failure_aborts_and_closes_partial_stream() -> None:
    stream_factory = StreamFactory(fail_start=True)
    with pytest.raises(OSError, match="synthetic PortAudio"):
        DebugSynth(SynthConfiguration(block_size=16), stream_factory=stream_factory)
    stream = stream_factory.streams[0]
    assert stream.start_count == 1
    assert stream.abort_count == 1
    assert stream.close_count == 1


@dataclass
class PanicProbeRuntime:
    node_id: UUID
    panic_count: int = 0
    panicked_generations: list[int] = field(default_factory=list)
    close_count: int = 0

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def panic(self, publish_generation: int) -> None:
        self.panic_count += 1
        self.panicked_generations.append(publish_generation)

    def close(self) -> None:
        self.close_count += 1


def _panic_probe_definition(instances: list[PanicProbeRuntime]) -> NodeDefinition:
    def factory(node_id: UUID) -> PanicProbeRuntime:
        runtime = PanicProbeRuntime(node_id)
        instances.append(runtime)
        return runtime

    return NodeDefinition(
        "test.panic_sink",
        1,
        "Panic Sink",
        "Test",
        "Records global panic propagation.",
        (),
        (),
        (),
        ExecutionKind.SINK,
        factory,
    )


@pytest.mark.parametrize("boundary", ["scheduler", "facade", "client"])
def test_global_panic_propagates_through_every_runtime_boundary(boundary: str) -> None:
    instances: list[PanicProbeRuntime] = []
    registry = NodeRegistry((_panic_probe_definition(instances),))
    document = GraphDocument()
    document.add_node("test.panic_sink", node_id=PANIC_NODE_ID)

    if boundary == "scheduler":
        plan = GraphCompiler(registry).compile(document.snapshot()).plan
        assert plan is not None
        owner: Scheduler | EngineFacade | InProcessEngineClient = Scheduler(plan)
    elif boundary == "facade":
        facade = EngineFacade(registry)
        assert facade.activate(document.snapshot()).plan is not None
        owner = facade
    else:
        client = InProcessEngineClient(registry)
        assert client.activate(document.snapshot()).activated
        owner = client

    owner.panic()
    assert len(instances) == 1
    assert instances[0].panic_count == 1
    # No tick has run, so the engine's publish generation is still 0: the
    # watermark, not a call alone, is what the runtime receives.
    assert instances[0].panicked_generations == [0]
    owner.close()
    assert instances[0].close_count == 1
