"""Callback-safe debug synth, Generate Audio sink, and global panic tests."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import numpy as np
import pytest
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.midi import DebugSynth, SynthConfiguration, SynthWaveform
from synesthesia_machine.midi.debug_synth import AudioCallback
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
from tests.phase1.helpers import frame_context, make_definition

CLOCK_ID = UUID("00000000-0000-0000-0000-000000000401")
MIDI_SOURCE_ID = UUID("00000000-0000-0000-0000-000000000402")
AUDIO_NODE_ID = UUID("00000000-0000-0000-0000-000000000403")
PANIC_NODE_ID = UUID("00000000-0000-0000-0000-000000000404")


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
    close_count: int

    def __init__(self, configuration: SynthConfiguration) -> None:
        self.configuration = configuration
        self.updates = []
        self.panic_count = 0
        self.close_count = 0

    def update(self, state: MidiStateFrame) -> None:
        self.updates.append(state)

    def panic(self) -> None:
        self.panic_count += 1

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


def _midi_state(notes: Mapping[tuple[int, int], int], *, tick_index: int = 1) -> MidiStateFrame:
    context = frame_context(clock_id=CLOCK_ID, tick_index=tick_index)
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
    runtime.process({"midi": midi}, enabled, midi.context)
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
    assert first.close_count == 1
    assert len(synth_factory.synths) == 2
    assert synth_factory.synths[1].configuration.volume == 0.4

    runtime.process({"midi": midi}, disabled, midi.context)
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


def test_audio_open_failure_becomes_recoverable_scheduler_error() -> None:
    def fail_factory(configuration: SynthConfiguration) -> RecordingSynth:
        del configuration
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

    result = Scheduler(plan).execute_tick(
        midi.context,
        source_values={PortKey(source_id, "value"): midi},
    )

    assert len(result.errors) == 1
    assert result.errors[0].node_id == audio_id
    assert result.errors[0].code == "audio_output_unavailable"
    assert result.errors[0].recoverable


def test_runtime_serializes_process_and_panic_snapshot_writers() -> None:
    entered_update = threading.Event()
    release_update = threading.Event()
    panic_started = threading.Event()
    order: list[str] = []
    errors: list[BaseException] = []

    class BlockingSynth(RecordingSynth):
        def update(self, state: MidiStateFrame) -> None:
            entered_update.set()
            if not release_update.wait(2.0):
                raise TimeoutError("test did not release blocked synth update")
            super().update(state)
            order.append("update")

        def panic(self) -> None:
            super().panic()
            order.append("panic")

    synth = BlockingSynth(SynthConfiguration())
    runtime = GenerateAudioRuntime(AUDIO_NODE_ID, synth_factory=lambda configuration: synth)
    definition = create_output_definitions()[0]
    parameters = _parameters(definition, {"enabled": True})
    midi = _midi_state({(0, 60): 100})

    def process() -> None:
        try:
            runtime.process({"midi": midi}, parameters, midi.context)
        except BaseException as error:
            errors.append(error)

    def panic() -> None:
        panic_started.set()
        runtime.panic()

    process_thread = threading.Thread(target=process)
    panic_thread = threading.Thread(target=panic)
    process_thread.start()
    assert entered_update.wait(1.0)
    panic_thread.start()
    assert panic_started.wait(1.0)
    assert synth.panic_count == 0
    release_update.set()
    process_thread.join(2.0)
    panic_thread.join(2.0)

    assert not process_thread.is_alive()
    assert not panic_thread.is_alive()
    assert errors == []
    assert order == ["update", "panic"]
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

    synth.panic()
    panicked = stream.render(configuration.block_size)
    assert np.count_nonzero(panicked) == 0
    assert synth.active_voice_count == 0
    assert np.count_nonzero(stream.render(configuration.block_size)) == 0

    synth.update(_midi_state({(0, 72): 100}, tick_index=2))
    assert np.any(stream.render(configuration.block_size) != 0.0)
    assert synth.active_keys == (72,)
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

    def panic(self) -> None:
        self.panic_count += 1

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
    owner.close()
    assert instances[0].close_count == 1
