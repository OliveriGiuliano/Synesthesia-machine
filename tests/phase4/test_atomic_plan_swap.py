"""Atomic plan replacement, state retention, and source ownership tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID

import pytest

from synesthesia_machine.contracts import (
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.graph import GraphDocument, LiteralValue
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
)
from synesthesia_machine.nodes.input import create_input_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.engine_facade import EngineFacade
from synesthesia_machine.runtime.execution_plan import CompiledNode, ExecutionPlan, PortKey
from synesthesia_machine.runtime.in_process_engine import InProcessEngineClient
from synesthesia_machine.runtime.scheduler import Scheduler
from tests.phase1.helpers import frame_context

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000004000")
SOURCE_A = UUID("00000000-0000-0000-0000-000000004001")
SOURCE_B = UUID("00000000-0000-0000-0000-000000004002")
STATE_NODE = UUID("00000000-0000-0000-0000-000000004010")
CANDIDATE_GOOD = UUID("00000000-0000-0000-0000-000000004020")
CANDIDATE_FAIL = UUID("00000000-0000-0000-0000-000000004030")


@dataclass(slots=True)
class _RuntimeRecord:
    process_count: int = 0
    reset_reasons: list[ResetReason] = field(default_factory=lambda: list[ResetReason]())
    close_count: int = 0
    parameters_seen: list[dict[str, ParameterValue]] = field(
        default_factory=lambda: list[dict[str, ParameterValue]]()
    )


class _LifecycleRuntime:
    def __init__(self, record: _RuntimeRecord) -> None:
        self.record = record

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        self.record.process_count += 1
        self.record.parameters_seen.append(dict(parameters))
        value = inputs.get("value", 1.0)
        gain = parameters.get("gain", 1.0)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("test lifecycle input must be numeric")
        if not isinstance(gain, (int, float)) or isinstance(gain, bool):
            raise TypeError("test lifecycle gain must be numeric")
        return {"value": float(value) * float(gain) + self.record.process_count}

    def reset(self, reason: ResetReason) -> None:
        self.record.reset_reasons.append(reason)

    def close(self) -> None:
        self.record.close_count += 1


@dataclass(slots=True)
class _TrackingRuntimeFactory:
    records: dict[UUID, list[_RuntimeRecord]] = field(
        default_factory=lambda: dict[UUID, list[_RuntimeRecord]]()
    )
    failure_message: str | None = None

    def __call__(self, node_id: UUID) -> _LifecycleRuntime:
        if self.failure_message is not None:
            raise RuntimeError(self.failure_message)
        record = _RuntimeRecord()
        self.records.setdefault(node_id, []).append(record)
        return _LifecycleRuntime(record)


def _definition(
    type_id: str,
    factory: Callable[[UUID], _LifecycleRuntime],
    *,
    execution_kind: ExecutionKind = ExecutionKind.STATEFUL,
    parameters: tuple[ParameterSpec, ...] = (),
    has_input: bool = True,
    implementation_version: int = 1,
) -> NodeDefinition:
    return NodeDefinition(
        type_id,
        implementation_version,
        type_id,
        "Test",
        "Atomic plan replacement test node.",
        (InputPortSpec("value", "Value", PortType.FLOAT),) if has_input else (),
        (OutputPortSpec("value", "Value", PortType.FLOAT),),
        parameters,
        execution_kind,
        factory,
        cache_policy=CachePolicy.NEVER,
    )


def _source_and_state_registry(
    source_factory: _TrackingRuntimeFactory,
    state_factory: _TrackingRuntimeFactory,
    *,
    source_parameters: tuple[ParameterSpec, ...] = (),
    state_parameters: tuple[ParameterSpec, ...] = (),
) -> NodeRegistry:
    return NodeRegistry(
        (
            _definition(
                "test.atomic.source",
                source_factory,
                execution_kind=ExecutionKind.SOURCE,
                parameters=source_parameters,
                has_input=False,
            ),
            _definition(
                "test.atomic.state",
                state_factory,
                parameters=state_parameters,
            ),
        )
    )


def _source_state_document(
    *, state_parameters: Mapping[str, LiteralValue] | None = None
) -> GraphDocument:
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("test.atomic.source", node_id=SOURCE_A)
    document.add_node(
        "test.atomic.state",
        node_id=STATE_NODE,
        parameters=state_parameters,
    )
    document.add_connection(SOURCE_A, "value", STATE_NODE, "value")
    return document


def test_live_parameter_change_retains_state_and_uses_new_compiled_value() -> None:
    source_factory = _TrackingRuntimeFactory()
    state_factory = _TrackingRuntimeFactory()
    registry = _source_and_state_registry(
        source_factory,
        state_factory,
        state_parameters=(
            ParameterSpec(
                "gain",
                "Gain",
                PortType.FLOAT,
                1.0,
                update_mode=ParameterUpdateMode.LIVE,
            ),
        ),
    )
    document = _source_state_document(state_parameters={"gain": 1.0})
    facade = EngineFacade(registry)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        first = facade.tick(
            frame_context(clock_id=SOURCE_A),
            source_values={PortKey(SOURCE_A, "value"): 2.0},
        )
        retained = state_factory.records[STATE_NODE][0]

        document.set_parameter(STATE_NODE, "gain", 3.0)
        assert facade.activate(document.snapshot()).plan is not None
        second = facade.tick(
            frame_context(clock_id=SOURCE_A, tick_index=2),
            source_values={PortKey(SOURCE_A, "value"): 2.0},
        )

        assert first.values[PortKey(STATE_NODE, "value")] == 3.0
        assert second.values[PortKey(STATE_NODE, "value")] == 8.0
        assert state_factory.records[STATE_NODE] == [retained]
        assert retained.parameters_seen == [{"gain": 1.0}, {"gain": 3.0}]
        assert retained.reset_reasons == []
        assert retained.close_count == 0
    finally:
        facade.close()


def test_recompile_parameter_replaces_and_retires_old_runtime() -> None:
    source_factory = _TrackingRuntimeFactory()
    state_factory = _TrackingRuntimeFactory()
    registry = _source_and_state_registry(
        source_factory,
        state_factory,
        state_parameters=(
            ParameterSpec(
                "mode",
                "Mode",
                PortType.STRING,
                "A",
                choices=("A", "B"),
                update_mode=ParameterUpdateMode.RECOMPILE,
            ),
        ),
    )
    document = _source_state_document(state_parameters={"mode": "A"})
    facade = EngineFacade(registry)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        facade.tick(
            frame_context(clock_id=SOURCE_A),
            source_values={PortKey(SOURCE_A, "value"): 2.0},
        )
        previous = state_factory.records[STATE_NODE][0]

        document.set_parameter(STATE_NODE, "mode", "B")
        assert facade.activate(document.snapshot()).plan is not None
        facade.tick(
            frame_context(clock_id=SOURCE_A, tick_index=2),
            source_values={PortKey(SOURCE_A, "value"): 2.0},
        )

        assert len(state_factory.records[STATE_NODE]) == 2
        assert previous.reset_reasons == [ResetReason.PLAN_REPLACED]
        assert previous.close_count == 1
        assert state_factory.records[STATE_NODE][1].process_count == 1
        assert len(source_factory.records[SOURCE_A]) == 1
    finally:
        facade.close()


def test_restart_source_parameter_invalidates_entire_source_component() -> None:
    source_factory = _TrackingRuntimeFactory()
    state_factory = _TrackingRuntimeFactory()
    registry = _source_and_state_registry(
        source_factory,
        state_factory,
        source_parameters=(
            ParameterSpec(
                "device",
                "Device",
                PortType.STRING,
                "A",
                update_mode=ParameterUpdateMode.RESTART_SOURCE,
            ),
        ),
    )
    document = _source_state_document()
    document.set_parameter(SOURCE_A, "device", "A")
    facade = EngineFacade(registry)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        old_source = source_factory.records[SOURCE_A][0]
        old_state = state_factory.records[STATE_NODE][0]

        document.set_parameter(SOURCE_A, "device", "B")
        assert facade.activate(document.snapshot()).plan is not None

        assert len(source_factory.records[SOURCE_A]) == 2
        assert len(state_factory.records[STATE_NODE]) == 2
        assert old_source.reset_reasons == [ResetReason.PLAN_REPLACED]
        assert old_source.close_count == 1
        assert old_state.reset_reasons == [ResetReason.PLAN_REPLACED]
        assert old_state.close_count == 1
    finally:
        facade.close()


def test_source_clock_change_replaces_only_clock_incompatible_state() -> None:
    source_factory = _TrackingRuntimeFactory()
    state_factory = _TrackingRuntimeFactory()
    registry = _source_and_state_registry(source_factory, state_factory)
    document = _source_state_document()
    document.add_node("test.atomic.source", node_id=SOURCE_B)
    facade = EngineFacade(registry)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        old_state = state_factory.records[STATE_NODE][0]

        document.add_connection(SOURCE_B, "value", STATE_NODE, "value")
        assert facade.activate(document.snapshot()).plan is not None

        assert len(source_factory.records[SOURCE_A]) == 1
        assert len(source_factory.records[SOURCE_B]) == 1
        assert len(state_factory.records[STATE_NODE]) == 2
        assert old_state.reset_reasons == [ResetReason.PLAN_REPLACED]
        assert old_state.close_count == 1
    finally:
        facade.close()


def test_implementation_version_change_is_not_runtime_compatible() -> None:
    factory = _TrackingRuntimeFactory()
    old_definition = _definition(
        "test.atomic.versioned",
        factory,
        has_input=False,
        implementation_version=1,
    )
    new_definition = replace(old_definition, implementation_version=2)
    old_node = CompiledNode(
        STATE_NODE,
        old_definition,
        clock_id=SOURCE_A,
        is_static=False,
    )
    new_node = replace(old_node, definition=new_definition)
    old_scheduler = Scheduler(ExecutionPlan(DOCUMENT_ID, 1, (old_node,), frozenset({STATE_NODE})))
    replacement = Scheduler.prepare_replacement(
        ExecutionPlan(DOCUMENT_ID, 2, (new_node,), frozenset({STATE_NODE})),
        old_scheduler,
    )
    try:
        assert old_node.state_retention_key != new_node.state_retention_key
        assert replacement.borrowed_runtime_ids == frozenset()
        assert len(factory.records[STATE_NODE]) == 2
        old_scheduler.close(ResetReason.PLAN_REPLACED)
        assert factory.records[STATE_NODE][0].reset_reasons == [ResetReason.PLAN_REPLACED]
        assert factory.records[STATE_NODE][0].close_count == 1
    finally:
        replacement.close()
        old_scheduler.close()


def test_runtime_factory_failure_disposes_candidate_and_leaves_old_plan_operational() -> None:
    active_factory = _TrackingRuntimeFactory()
    candidate_factory = _TrackingRuntimeFactory()
    failing_factory = _TrackingRuntimeFactory(failure_message="candidate factory failed")
    registry = NodeRegistry(
        (
            _definition("test.atomic.active", active_factory, has_input=False),
            _definition("test.atomic.candidate", candidate_factory, has_input=False),
            _definition("test.atomic.failure", failing_factory, has_input=False),
        )
    )
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("test.atomic.active", node_id=SOURCE_A)
    facade = EngineFacade(registry)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        old_plan = facade.active_plan
        facade.tick(frame_context(clock_id=SOURCE_A))
        active_record = active_factory.records[SOURCE_A][0]

        document.add_node("test.atomic.candidate", node_id=CANDIDATE_GOOD)
        document.add_node("test.atomic.failure", node_id=CANDIDATE_FAIL)
        with pytest.raises(RuntimeError, match="candidate factory failed"):
            facade.activate(document.snapshot())

        assert facade.active_plan is old_plan
        assert active_record.reset_reasons == []
        assert active_record.close_count == 0
        assert candidate_factory.records[CANDIDATE_GOOD][0].close_count == 1
        result = facade.tick(frame_context(clock_id=SOURCE_A, tick_index=2))
        assert result.values[PortKey(SOURCE_A, "value")] == 3.0
        assert active_record.process_count == 2
    finally:
        facade.close()


def test_engine_restart_replaces_all_runtimes_with_engine_reset_reason() -> None:
    factory = _TrackingRuntimeFactory()
    registry = NodeRegistry((_definition("test.atomic.restart", factory, has_input=False),))
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("test.atomic.restart", node_id=STATE_NODE)
    facade = EngineFacade(registry)
    try:
        snapshot = document.snapshot()
        assert facade.activate(snapshot).plan is not None
        previous = factory.records[STATE_NODE][0]

        assert facade.activate(snapshot, reset_reason=ResetReason.ENGINE_RESTARTED).plan is not None

        assert len(factory.records[STATE_NODE]) == 2
        assert previous.reset_reasons == [ResetReason.ENGINE_RESTARTED]
        assert previous.close_count == 1
    finally:
        facade.close()


class _FakeVideoSource:
    def __init__(self, node_id: UUID, file_path: str, events: list[str]) -> None:
        self.node_id = node_id
        self.file_path = file_path
        self.events = events
        self.state = SourceState.READY
        self.fail_pause = False
        self.close_count = 0

    def play(self) -> None:
        self.events.append(f"{self.file_path}:play")
        self.state = SourceState.PLAYING

    def pause(self) -> None:
        self.events.append(f"{self.file_path}:pause")
        if self.fail_pause:
            raise RuntimeError("source pause failed")
        if self.state is SourceState.PLAYING:
            self.state = SourceState.PAUSED

    def resume(self) -> None:
        self.events.append(f"{self.file_path}:resume")
        if self.state is SourceState.PAUSED:
            self.state = SourceState.PLAYING

    def stop(self) -> None:
        self.state = SourceState.STOPPED

    def reload(self) -> None:
        self.state = SourceState.READY

    def seek(self, source_time_s: float) -> None:
        del source_time_s
        raise NotImplementedError

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        return SourceStatus(
            self.node_id,
            self.state,
            file_path=self.file_path,
            dropped_before_processing=dropped_before_processing,
        )

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        self.events.append(f"{self.file_path}:close")
        self.close_count += 1
        self.state = SourceState.CLOSED


@dataclass(slots=True)
class _FakeVideoFactory:
    sources: list[_FakeVideoSource] = field(default_factory=lambda: list[_FakeVideoSource]())
    events: list[str] = field(default_factory=lambda: list[str]())

    def __call__(
        self,
        node_id: UUID,
        file_path: str | Path,
        *,
        process_every_nth_frame: int,
        loop: bool,
        stream_index: int,
        on_frame: object,
        on_reset: object,
    ) -> _FakeVideoSource:
        del process_every_nth_frame, loop, stream_index, on_frame, on_reset
        source = _FakeVideoSource(node_id, str(file_path), self.events)
        self.sources.append(source)
        return source


def _video_document(file_path: str = "first.mp4") -> tuple[GraphDocument, UUID]:
    document = GraphDocument(document_id=DOCUMENT_ID)
    source_id = document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_A,
        parameters={"file_path": file_path},
    )
    return document, source_id


def test_source_controller_retention_restart_and_new_source_start_policy() -> None:
    factory = _FakeVideoFactory()
    client = InProcessEngineClient(
        NodeRegistry(create_input_definitions()),
        video_source_factory=factory,
    )
    document, source_id = _video_document()
    try:
        assert client.activate(document.snapshot()).activated
        original = factory.sources[0]
        client.play(source_id)

        document.set_position(source_id, (10.0, 20.0))
        assert client.activate(document.snapshot()).activated
        assert factory.sources == [original]
        assert original.state is SourceState.PLAYING
        assert factory.events[-2:] == ["first.mp4:pause", "first.mp4:resume"]

        document.set_parameter(source_id, "file_path", "replacement.mp4")
        assert client.activate(document.snapshot()).activated
        replacement_source = factory.sources[1]
        assert original.close_count == 1
        assert replacement_source.state is SourceState.PLAYING
        assert factory.events[-3:] == [
            "first.mp4:pause",
            "first.mp4:close",
            "replacement.mp4:play",
        ]

        new_source_id = document.add_node(
            "synmachine.input.load_video",
            node_id=SOURCE_B,
            parameters={"file_path": "new.mp4"},
        )
        assert client.activate(document.snapshot()).activated
        statuses = {status.node_id: status for status in client.source_status()}
        assert statuses[source_id].state is SourceState.PLAYING
        assert statuses[new_source_id].state is SourceState.READY
    finally:
        client.close()


def test_quiesce_failure_rolls_back_sources_runtimes_and_revision() -> None:
    runtime_factory = _TrackingRuntimeFactory()
    load_video = replace(
        create_input_definitions()[0],
        runtime_factory=runtime_factory,
    )
    source_factory = _FakeVideoFactory()
    client = InProcessEngineClient(
        NodeRegistry((load_video,)),
        video_source_factory=source_factory,
    )
    document, source_id = _video_document()
    try:
        valid_snapshot = document.snapshot()
        assert client.activate(valid_snapshot).activated
        active_source = source_factory.sources[0]
        active_runtime = runtime_factory.records[source_id][0]
        client.play(source_id)
        active_source.fail_pause = True

        document.set_parameter(source_id, "file_path", "candidate.mp4")
        with pytest.raises(RuntimeError, match="source pause failed"):
            client.activate(document.snapshot())

        candidate_source = source_factory.sources[1]
        candidate_runtime = runtime_factory.records[source_id][1]
        assert client.status().graph_revision == valid_snapshot.revision
        assert client.source_status(source_id)[0].state is SourceState.PLAYING
        assert active_source.close_count == 0
        assert active_runtime.close_count == 0
        assert active_runtime.reset_reasons == []
        assert candidate_source.close_count == 1
        assert candidate_runtime.close_count == 1
        assert candidate_runtime.reset_reasons == []
    finally:
        if source_factory.sources:
            source_factory.sources[0].fail_pause = False
        client.close()
