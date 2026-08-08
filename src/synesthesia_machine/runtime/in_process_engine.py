"""Phase 3 final-shaped in-process engine client and latest-frame graph worker."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psutil

from synesthesia_machine.contracts import (
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NodeProfile,
    NotePreview,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.media.camera_source import CameraBackendPreference, CameraSourceService
from synesthesia_machine.media.video_source import PresentedSourceFrame, VideoSourceService
from synesthesia_machine.nodes.base import ResetReason
from synesthesia_machine.nodes.input import LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.engine_facade import EngineFacade
from synesthesia_machine.runtime.execution_plan import CompiledNode, ExecutionPlan, PortKey
from synesthesia_machine.runtime.previews import PreviewBroker
from synesthesia_machine.runtime.profiling import RuntimeProfiler
from synesthesia_machine.runtime.scheduler import TickResult


class SourceController(Protocol):
    node_id: UUID

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    def reload(self) -> None: ...

    def seek(self, source_time_s: float) -> None: ...

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus: ...

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool: ...

    def close(self) -> None: ...


class VideoSourceFactory(Protocol):
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
    ) -> SourceController: ...


class CameraSourceFactory(Protocol):
    def __call__(
        self,
        node_id: UUID,
        device_id: str,
        *,
        requested_width: int,
        requested_height: int,
        requested_fps: float,
        backend_preference: CameraBackendPreference,
        process_every_nth_frame: int,
        reconnect_automatically: bool,
        on_frame: object,
        on_reset: object,
    ) -> SourceController: ...


@dataclass(frozen=True, slots=True)
class _TickCommand:
    source_node_id: UUID
    frame: PresentedSourceFrame


@dataclass(frozen=True, slots=True)
class _ResetCommand:
    source_node_id: UUID
    reason: ResetReason


type _GraphCommand = _TickCommand | _ResetCommand

_SOURCE_MAILBOX_CAPACITY = 2
_GRAPH_LATENCY_WINDOW = 240
_FPS_WINDOW_NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class SourceMailboxMetrics:
    """Current bounded-mailbox state and latest completed-frame timing for one source."""

    occupancy: int
    capacity: int
    dropped_before_processing: int
    processing_latency_ms: float
    frame_age_ms: float


class LatestFrameGraphWorker:
    """Serialize scheduler access while retaining at most one pending tick per source."""

    def __init__(
        self,
        facade: EngineFacade,
        *,
        preview_broker: PreviewBroker | None = None,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        execution_clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._facade = facade
        self._preview_broker = preview_broker
        self._monotonic_ns = monotonic_ns
        self._execution_clock_ns = execution_clock_ns
        self._condition = threading.Condition()
        self._commands: deque[_GraphCommand] = deque()
        self._busy = False
        self._active_tick_source_id: UUID | None = None
        self._closed = False
        self._processed_ticks = 0
        self._dropped_by_source: dict[UUID, int] = {}
        self._last_completed_received_ns: dict[UUID, int] = {}
        self._last_processing_latency_ms: dict[UUID, float] = {}
        self._graph_durations_ns: deque[int] = deque(maxlen=_GRAPH_LATENCY_WINDOW)
        self._input_times_ns: deque[int] = deque(maxlen=240)
        self._processed_times_ns: deque[int] = deque(maxlen=240)
        self._last_result: TickResult | None = None
        self._last_error: str | None = None
        self._started_ns: int | None = None
        self._last_tick_ns: int | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="in-process-graph-worker",
            daemon=True,
        )
        self._thread.start()

    @property
    def processed_ticks(self) -> int:
        with self._condition:
            return self._processed_ticks

    @property
    def last_result(self) -> TickResult | None:
        with self._condition:
            return self._last_result

    @property
    def last_error(self) -> str | None:
        with self._condition:
            return self._last_error

    @property
    def elapsed_s(self) -> float:
        with self._condition:
            if self._started_ns is None or self._last_tick_ns is None:
                return 0.0
            return max(0.0, (self._last_tick_ns - self._started_ns) / 1_000_000_000)

    @property
    def input_fps(self) -> float:
        with self._condition:
            return _rolling_event_rate(self._input_times_ns, self._monotonic_ns())

    @property
    def processed_fps(self) -> float:
        with self._condition:
            return _rolling_event_rate(self._processed_times_ns, self._monotonic_ns())

    def publish(self, source_node_id: UUID, frame: PresentedSourceFrame) -> None:
        if frame.context.clock_id != source_node_id:
            raise ValueError("presented frame clock does not match its source node")
        command = _TickCommand(source_node_id, frame)
        with self._condition:
            self._ensure_open()
            self._input_times_ns.append(frame.context.received_monotonic_ns)
            for index in range(len(self._commands) - 1, -1, -1):
                pending = self._commands[index]
                if isinstance(pending, _ResetCommand) and pending.source_node_id == source_node_id:
                    break
                if isinstance(pending, _TickCommand) and pending.source_node_id == source_node_id:
                    self._commands[index] = command
                    self._dropped_by_source[source_node_id] = (
                        self._dropped_by_source.get(source_node_id, 0) + 1
                    )
                    self._condition.notify()
                    return
            self._commands.append(command)
            self._condition.notify()

    def reset_source(self, source_node_id: UUID, reason: ResetReason) -> None:
        with self._condition:
            self._ensure_open()
            self._commands = deque(
                command
                for command in self._commands
                if not (
                    isinstance(command, _TickCommand) and command.source_node_id == source_node_id
                )
            )
            self._commands.append(_ResetCommand(source_node_id, reason))
            self._condition.notify()

    def dropped_for(self, source_node_id: UUID) -> int:
        with self._condition:
            return self._dropped_by_source.get(source_node_id, 0)

    def total_dropped(self) -> int:
        with self._condition:
            return sum(self._dropped_by_source.values())

    def graph_execution_metrics(self) -> tuple[int, float, float, float, float]:
        with self._condition:
            ordered = tuple(sorted(self._graph_durations_ns))
        if not ordered:
            return (0, 0.0, 0.0, 0.0, 0.0)
        return (
            len(ordered),
            _duration_percentile_ms(ordered, 50.0),
            _duration_percentile_ms(ordered, 95.0),
            _duration_percentile_ms(ordered, 99.0),
            ordered[-1] / 1_000_000.0,
        )

    def reset_graph_execution_metrics(self) -> None:
        with self._condition:
            self._graph_durations_ns.clear()
            self._input_times_ns.clear()
            self._processed_times_ns.clear()

    def mailbox_metrics(self, source_node_id: UUID) -> SourceMailboxMetrics:
        """Return an atomic snapshot of one source's two-slot latest-frame mailbox."""

        with self._condition:
            occupancy = int(self._active_tick_source_id == source_node_id) + sum(
                isinstance(command, _TickCommand) and command.source_node_id == source_node_id
                for command in self._commands
            )
            received_ns = self._last_completed_received_ns.get(source_node_id)
            frame_age_ms = (
                max(0.0, (self._monotonic_ns() - received_ns) / 1_000_000)
                if received_ns is not None
                else 0.0
            )
            return SourceMailboxMetrics(
                occupancy=occupancy,
                capacity=_SOURCE_MAILBOX_CAPACITY,
                dropped_before_processing=self._dropped_by_source.get(source_node_id, 0),
                processing_latency_ms=self._last_processing_latency_ms.get(source_node_id, 0.0),
                frame_age_ms=frame_age_ms,
            )

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._condition:
            while self._commands or self._busy:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def reset_plan_metrics(self) -> None:
        """Clear plan-scoped diagnostics after an atomic swap has committed."""

        with self._condition:
            self._processed_ticks = 0
            self._dropped_by_source.clear()
            self._last_completed_received_ns.clear()
            self._last_processing_latency_ms.clear()
            self._graph_durations_ns.clear()
            self._last_result = None
            self._last_error = None
            self._started_ns = None
            self._last_tick_ns = None

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._commands.clear()
            self._condition.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._commands and not self._closed:
                    self._condition.wait()
                if self._closed:
                    self._busy = False
                    self._condition.notify_all()
                    return
                command = self._commands.popleft()
                self._busy = True
                self._active_tick_source_id = (
                    command.source_node_id if isinstance(command, _TickCommand) else None
                )
            try:
                if isinstance(command, _ResetCommand):
                    self._facade.reset_source(command.source_node_id, command.reason)
                else:
                    frame = command.frame
                    graph_started_ns = self._execution_clock_ns()
                    result = self._facade.tick(
                        frame.context,
                        source_values={
                            PortKey(command.source_node_id, "image"): frame.image,
                            PortKey(command.source_node_id, "processed_index"): (
                                frame.processed_index
                            ),
                        },
                    )
                    if self._preview_broker is not None:
                        self._preview_broker.publish(result)
                    completed_ns = self._monotonic_ns()
                    graph_duration_ns = max(0, self._execution_clock_ns() - graph_started_ns)
                    with self._condition:
                        if self._started_ns is None:
                            self._started_ns = completed_ns
                        self._last_tick_ns = completed_ns
                        self._processed_ticks += 1
                        self._processed_times_ns.append(completed_ns)
                        self._last_result = result
                        self._graph_durations_ns.append(graph_duration_ns)
                        received_ns = frame.context.received_monotonic_ns
                        self._last_completed_received_ns[command.source_node_id] = received_ns
                        self._last_processing_latency_ms[command.source_node_id] = max(
                            0.0, (completed_ns - received_ns) / 1_000_000
                        )
            except Exception as error:
                with self._condition:
                    self._last_error = str(error)
            finally:
                with self._condition:
                    self._busy = False
                    self._active_tick_source_id = None
                    self._condition.notify_all()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Graph worker is closed")


def _duration_percentile_ms(ordered_ns: tuple[int, ...], percentile: float) -> float:
    position = (len(ordered_ns) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        value_ns = float(ordered_ns[lower])
    else:
        fraction = position - lower
        value_ns = ordered_ns[lower] * (1.0 - fraction) + ordered_ns[upper] * fraction
    return value_ns / 1_000_000.0


def _rolling_event_rate(events_ns: deque[int], now_ns: int) -> float:
    cutoff_ns = now_ns - _FPS_WINDOW_NS
    while events_ns and events_ns[0] < cutoff_ns:
        events_ns.popleft()
    return float(len(events_ns))


class _FailedSource:
    """Status-preserving source used when source preparation fails during activation."""

    def __init__(
        self,
        node_id: UUID,
        error: Exception,
        *,
        source_kind: str,
        file_path: str = "",
        device_id: str | None = None,
    ) -> None:
        self.node_id = node_id
        self._file_path = file_path
        self._source_kind = source_kind
        self._device_id = device_id
        self._error = str(error)
        self._state = SourceState.ERROR

    def play(self) -> None:
        raise RuntimeError(self._error)

    def pause(self) -> None:
        return

    def resume(self) -> None:
        return

    def stop(self) -> None:
        if self._state is not SourceState.CLOSED:
            self._state = SourceState.STOPPED

    def reload(self) -> None:
        self._state = SourceState.ERROR

    def seek(self, source_time_s: float) -> None:
        del source_time_s
        raise NotImplementedError(f"{self._source_kind.title()} source cannot seek")

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        return SourceStatus(
            node_id=self.node_id,
            state=self._state,
            file_path=self._file_path,
            dropped_before_processing=dropped_before_processing,
            warnings=1,
            last_error=self._error,
            source_kind=self._source_kind,
            device_id=self._device_id,
        )

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        self._state = SourceState.CLOSED


class InProcessEngineClient:
    """Phase 3 implementation of the transport-independent EngineClient protocol."""

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        video_source_factory: VideoSourceFactory | None = None,
        camera_source_factory: CameraSourceFactory | None = None,
        worker_clock: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._lock = threading.RLock()
        self._profiler = RuntimeProfiler()
        self._facade = EngineFacade(registry)
        self._profiling_enabled = False
        self._video_source_factory = video_source_factory or VideoSourceService
        self._camera_source_factory = camera_source_factory or CameraSourceService
        self._worker_clock = worker_clock
        self._preview_broker = PreviewBroker()
        self._worker: LatestFrameGraphWorker | None = None
        self._sources: dict[UUID, SourceController] = {}
        self._state = EngineState.STOPPED
        self._graph_revision: int | None = None
        self._latest_valid_snapshot: GraphSnapshot | None = None
        self._latest_demand_roots: tuple[UUID, ...] | None = None

    def activate(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
        reset_reason: ResetReason = ResetReason.PLAN_REPLACED,
    ) -> EngineActivation:
        with self._lock:
            self._ensure_open()
            roots = None if demand_roots is None else tuple(sorted(demand_roots, key=str))
            prepared = self._facade.prepare(
                snapshot,
                demand_roots=roots,
                reset_reason=reset_reason,
            )
            plan = prepared.plan
            if plan is None:
                return EngineActivation(snapshot.revision, prepared.result.report, False)
            try:
                preview_configuration = self._preview_broker.prepare(plan)
            except Exception:
                prepared.close()
                raise

            worker = self._worker
            worker_created = worker is None
            if worker is None:
                worker = LatestFrameGraphWorker(
                    self._facade,
                    preview_broker=self._preview_broker,
                    monotonic_ns=self._worker_clock,
                )
            old_plan = self._facade.active_plan
            reusable_sources = self._reusable_source_ids(old_plan, plan, reset_reason)
            sources: dict[UUID, SourceController] = {}
            try:
                for node in plan.nodes:
                    if node.definition.type_id not in {LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID}:
                        continue
                    existing = self._sources.get(node.node_id)
                    sources[node.node_id] = (
                        existing
                        if existing is not None and node.node_id in reusable_sources
                        else self._create_source(node, worker)
                    )
            except Exception:
                self._dispose_candidate_sources(sources, reusable_sources)
                if worker_created:
                    worker.close()
                prepared.close()
                raise

            old_states = {
                node_id: source.status().state for node_id, source in self._sources.items()
            }
            paused_for_swap: list[SourceController] = []
            try:
                for node_id, source in self._sources.items():
                    if old_states[node_id] in {SourceState.PLAYING, SourceState.RECONNECTING}:
                        source.pause()
                        paused_for_swap.append(source)
                if self._worker is not None and not self._worker.wait_until_idle(5.0):
                    raise TimeoutError("Active graph did not reach a safe plan-swap boundary")
                self._facade.commit(prepared, reset_reason=reset_reason)
            except Exception:
                for source in paused_for_swap:
                    with suppress(Exception):
                        source.resume()
                self._dispose_candidate_sources(sources, reusable_sources)
                if worker_created:
                    worker.close()
                prepared.close()
                raise

            previous_sources = self._sources
            previous_state = self._state
            self._worker = worker
            self._sources = sources
            self._preview_broker.apply(preview_configuration)
            worker.reset_plan_metrics()
            self._graph_revision = snapshot.revision
            self._latest_valid_snapshot = snapshot
            self._latest_demand_roots = roots
            self._state = previous_state if old_plan is not None else EngineState.STOPPED
            self._profiler.reset()
            for node_id, source in previous_sources.items():
                if node_id not in reusable_sources:
                    with suppress(Exception):
                        source.close()
            if previous_state is EngineState.RUNNING:
                for node_id, source in sources.items():
                    if old_states.get(node_id) in {SourceState.PLAYING, SourceState.RECONNECTING}:
                        with suppress(Exception):
                            source.resume() if node_id in reusable_sources else source.play()
            return EngineActivation(snapshot.revision, prepared.result.report, True)

    def play(self, source_node_id: UUID | None = None) -> None:
        with self._lock:
            sources = self._selected_sources(source_node_id)
            for source in sources:
                source.play()
            self._state = EngineState.RUNNING

    def pause(self, source_node_id: UUID | None = None) -> None:
        with self._lock:
            sources = self._selected_sources(source_node_id)
            for source in sources:
                source.pause()
            self._state = EngineState.PAUSED

    def resume(self, source_node_id: UUID | None = None) -> None:
        with self._lock:
            sources = self._selected_sources(source_node_id)
            for source in sources:
                source.resume()
            self._state = EngineState.RUNNING

    def stop(self, source_node_id: UUID | None = None) -> None:
        with self._lock:
            sources = self._selected_sources(source_node_id)
            for source in sources:
                source.stop()
            self._facade.panic()
            self._state = EngineState.STOPPED

    def reload(self, source_node_id: UUID | None = None) -> None:
        with self._lock:
            sources = self._selected_sources(source_node_id)
            for source in sources:
                source.reload()
            self._facade.panic()
            self._state = EngineState.STOPPED

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        with self._lock:
            self._selected_sources(source_node_id)[0].seek(source_time_s)

    def panic(self) -> None:
        with self._lock:
            self._ensure_open()
            self._facade.panic()

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        with self._lock:
            worker = self._worker
            statuses: list[SourceStatus] = []
            for source in self._selected_sources(source_node_id):
                status = source.status()
                if worker is not None:
                    mailbox = worker.mailbox_metrics(source.node_id)
                    status = replace(
                        status,
                        dropped_before_processing=mailbox.dropped_before_processing,
                        mailbox_occupancy=mailbox.occupancy,
                        frame_age_ms=mailbox.frame_age_ms,
                        mailbox_capacity=mailbox.capacity,
                        processing_latency_ms=mailbox.processing_latency_ms,
                    )
                statuses.append(status)
            return tuple(statuses)

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        with self._lock:
            self._ensure_open()
            return self._facade.midi_output_status(output_node_id)

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        with self._lock:
            self._ensure_open()
            return self._facade.node_memory_diagnostics(node_id)

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        return self._profiler.profiles()

    def set_profiling_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._ensure_open()
            if enabled == self._profiling_enabled:
                return
            self._profiling_enabled = enabled
            self._profiler.reset()
            self._facade.set_profiling_hook(self._profiler.record if enabled else None)

    def reset_profiling(self) -> None:
        self._profiler.reset()
        worker = self._worker
        if worker is not None:
            worker.reset_graph_execution_metrics()

    def metrics(self) -> EngineMetrics:
        with self._lock:
            if self._state is EngineState.CLOSED:
                return EngineMetrics(state=EngineState.CLOSED, graph_revision=self._graph_revision)
            worker = self._worker
            statuses = self.source_status()
            state = self._effective_state(statuses, worker)
            processed_ticks = worker.processed_ticks if worker is not None else 0
            input_fps = worker.input_fps if worker is not None else 0.0
            processed_fps = worker.processed_fps if worker is not None else 0.0
            p95_ms = self._profiler.global_p95_ms()
            graph_window, graph_p50, graph_p95, graph_p99, graph_max = (
                worker.graph_execution_metrics() if worker is not None else (0, 0.0, 0.0, 0.0, 0.0)
            )
            return EngineMetrics(
                state=state,
                graph_revision=self._graph_revision,
                processed_ticks=processed_ticks,
                input_fps=input_fps,
                processed_fps=processed_fps,
                p95_node_time_ms=p95_ms,
                dropped_before_processing=(worker.total_dropped() if worker is not None else 0),
                skipped_by_selection=sum(status.skipped_by_selection for status in statuses),
                memory_bytes=psutil.Process().memory_info().rss,
                mailbox_occupancy=sum(status.mailbox_occupancy for status in statuses),
                mailbox_capacity=sum(status.mailbox_capacity for status in statuses),
                preview_fps=self._preview_broker.preview_fps(),
                frame_age_ms=max((status.frame_age_ms for status in statuses), default=0.0),
                processing_latency_ms=max(
                    (status.processing_latency_ms for status in statuses), default=0.0
                ),
                graph_latency_window_size=graph_window,
                p50_graph_execution_ms=graph_p50,
                p95_graph_execution_ms=graph_p95,
                p99_graph_execution_ms=graph_p99,
                max_graph_execution_ms=graph_max,
            )

    def poll_image_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        return self._preview_broker.poll_images(after_sequences)

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        return self._preview_broker.poll_notes(after_sequences)

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._lock:
            sources = tuple(self._sources.values())
            worker = self._worker
        for source in sources:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0 or not source.wait_until_finished(remaining):
                return False
        if worker is None:
            return True
        return worker.wait_until_idle(max(0.0, deadline - time.monotonic()))

    def status(self) -> EngineStatus:
        with self._lock:
            connection_state = (
                EngineConnectionState.CLOSED
                if self._state is EngineState.CLOSED
                else EngineConnectionState.CONNECTED
            )
            return EngineStatus(connection_state, graph_revision=self._graph_revision)

    def restart(self) -> EngineActivation | None:
        with self._lock:
            self._ensure_open()
            snapshot = self._latest_valid_snapshot
            demand_roots = self._latest_demand_roots
        if snapshot is None:
            return None
        return self.activate(
            snapshot,
            demand_roots=demand_roots,
            reset_reason=ResetReason.ENGINE_RESTARTED,
        )

    def close(self) -> None:
        with self._lock:
            if self._state is EngineState.CLOSED:
                return
            self._close_runtime()
            self._facade.close()
            self._state = EngineState.CLOSED

    def _create_source(
        self, node: CompiledNode, worker: LatestFrameGraphWorker
    ) -> SourceController:
        if node.definition.type_id == LOAD_CAMERA_TYPE_ID:
            return self._create_camera_source(node, worker)
        return self._create_video_source(node, worker)

    def _create_video_source(
        self, node: CompiledNode, worker: LatestFrameGraphWorker
    ) -> SourceController:
        file_path = _text_parameter(node, "file_path")
        try:
            return self._video_source_factory(
                node.node_id,
                file_path,
                process_every_nth_frame=_int_parameter(node, "process_every_nth_frame"),
                loop=_bool_parameter(node, "loop"),
                stream_index=_int_parameter(node, "stream_index"),
                on_frame=partial(worker.publish, node.node_id),
                on_reset=partial(worker.reset_source, node.node_id),
            )
        except Exception as error:
            return _FailedSource(node.node_id, error, source_kind="video", file_path=file_path)

    def _create_camera_source(
        self, node: CompiledNode, worker: LatestFrameGraphWorker
    ) -> SourceController:
        device_id = _text_parameter(node, "device_id")
        try:
            return self._camera_source_factory(
                node.node_id,
                device_id,
                requested_width=_int_parameter(node, "requested_width"),
                requested_height=_int_parameter(node, "requested_height"),
                requested_fps=_float_parameter(node, "requested_fps"),
                backend_preference=CameraBackendPreference(
                    _text_parameter(node, "backend_preference")
                ),
                process_every_nth_frame=_int_parameter(node, "process_every_nth_frame"),
                reconnect_automatically=_bool_parameter(node, "reconnect_automatically"),
                on_frame=partial(worker.publish, node.node_id),
                on_reset=partial(worker.reset_source, node.node_id),
            )
        except Exception as error:
            return _FailedSource(
                node.node_id,
                error,
                source_kind="camera",
                device_id=device_id,
            )

    @staticmethod
    def _reusable_source_ids(
        old_plan: ExecutionPlan | None,
        new_plan: ExecutionPlan,
        reset_reason: ResetReason,
    ) -> frozenset[UUID]:
        if reset_reason is not ResetReason.PLAN_REPLACED or old_plan is None:
            return frozenset()
        old_sources = {
            node.node_id: node
            for node in old_plan.nodes
            if node.definition.type_id in {LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID}
        }
        return frozenset(
            node.node_id
            for node in new_plan.nodes
            if node.definition.type_id in {LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID}
            and (old := old_sources.get(node.node_id)) is not None
            and old.state_retention_key == node.state_retention_key
        )

    @staticmethod
    def _dispose_candidate_sources(
        sources: Mapping[UUID, SourceController], reusable_source_ids: frozenset[UUID]
    ) -> None:
        for node_id, source in sources.items():
            if node_id not in reusable_source_ids:
                with suppress(Exception):
                    source.close()

    def _selected_sources(self, source_node_id: UUID | None) -> tuple[SourceController, ...]:
        self._ensure_open()
        if source_node_id is None:
            return tuple(self._sources[key] for key in sorted(self._sources, key=str))
        source = self._sources.get(source_node_id)
        if source is None:
            raise KeyError(f"Unknown source node: {source_node_id}")
        return (source,)

    def _close_runtime(self) -> None:
        sources = tuple(self._sources.values())
        self._sources = {}
        for source in sources:
            source.close()
        if self._worker is not None:
            self._worker.close()
            self._worker = None
        self._preview_broker.clear()

    def _effective_state(
        self,
        statuses: tuple[SourceStatus, ...],
        worker: LatestFrameGraphWorker | None,
    ) -> EngineState:
        if worker is not None and worker.last_error is not None:
            return EngineState.ERROR
        if any(status.state is SourceState.ERROR for status in statuses):
            return EngineState.ERROR
        if statuses and all(status.state is SourceState.ENDED for status in statuses):
            return EngineState.STOPPED
        return self._state

    def _ensure_open(self) -> None:
        if self._state is EngineState.CLOSED:
            raise RuntimeError("Engine client is closed")


def _text_parameter(node: CompiledNode, parameter_id: str) -> str:
    value = node.parameters[parameter_id]
    if not isinstance(value, str):
        raise TypeError(f"Expected string parameter {parameter_id!r}")
    return value


def _int_parameter(node: CompiledNode, parameter_id: str) -> int:
    value = node.parameters[parameter_id]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Expected integer parameter {parameter_id!r}")
    return value


def _float_parameter(node: CompiledNode, parameter_id: str) -> float:
    value = node.parameters[parameter_id]
    if not isinstance(value, float):
        raise TypeError(f"Expected float parameter {parameter_id!r}")
    return value


def _bool_parameter(node: CompiledNode, parameter_id: str) -> bool:
    value = node.parameters[parameter_id]
    if not isinstance(value, bool):
        raise TypeError(f"Expected boolean parameter {parameter_id!r}")
    return value


__all__ = [
    "CameraSourceFactory",
    "InProcessEngineClient",
    "LatestFrameGraphWorker",
    "SourceController",
    "SourceMailboxMetrics",
    "VideoSourceFactory",
]
