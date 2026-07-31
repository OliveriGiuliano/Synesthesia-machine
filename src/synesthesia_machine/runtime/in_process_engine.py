"""Phase 3 final-shaped in-process engine client and latest-frame graph worker."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol
from uuid import UUID

import numpy as np
import psutil

from synesthesia_machine.contracts import (
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    NotePreview,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.media.video_source import PresentedVideoFrame, VideoSourceService
from synesthesia_machine.nodes.base import ResetReason
from synesthesia_machine.nodes.input import LOAD_VIDEO_TYPE_ID
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.engine_facade import EngineFacade
from synesthesia_machine.runtime.execution_plan import CompiledNode, ExecutionPlan, PortKey
from synesthesia_machine.runtime.previews import PreviewBroker
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


@dataclass(frozen=True, slots=True)
class _TickCommand:
    source_node_id: UUID
    frame: PresentedVideoFrame


@dataclass(frozen=True, slots=True)
class _ResetCommand:
    source_node_id: UUID
    reason: ResetReason


type _GraphCommand = _TickCommand | _ResetCommand


class LatestFrameGraphWorker:
    """Serialize scheduler access while retaining at most one pending tick per source."""

    def __init__(
        self, facade: EngineFacade, *, preview_broker: PreviewBroker | None = None
    ) -> None:
        self._facade = facade
        self._preview_broker = preview_broker
        self._condition = threading.Condition()
        self._commands: deque[_GraphCommand] = deque()
        self._busy = False
        self._closed = False
        self._processed_ticks = 0
        self._dropped_by_source: dict[UUID, int] = {}
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

    def publish(self, source_node_id: UUID, frame: PresentedVideoFrame) -> None:
        if frame.image.context.clock_id != source_node_id:
            raise ValueError("presented frame clock does not match its source node")
        command = _TickCommand(source_node_id, frame)
        with self._condition:
            self._ensure_open()
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
            try:
                if isinstance(command, _ResetCommand):
                    self._facade.reset_source(command.source_node_id, command.reason)
                else:
                    frame = command.frame
                    result = self._facade.tick(
                        frame.image.context,
                        source_values={
                            PortKey(command.source_node_id, "image"): frame.image,
                            PortKey(command.source_node_id, "processed_index"): (
                                frame.processed_index
                            ),
                        },
                    )
                    if self._preview_broker is not None:
                        self._preview_broker.publish(result)
                    completed_ns = time.perf_counter_ns()
                    with self._condition:
                        if self._started_ns is None:
                            self._started_ns = completed_ns
                        self._last_tick_ns = completed_ns
                        self._processed_ticks += 1
                        self._last_result = result
            except Exception as error:
                with self._condition:
                    self._last_error = str(error)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Graph worker is closed")


class _FailedVideoSource:
    """Status-preserving source used when metadata opening fails during activation."""

    def __init__(self, node_id: UUID, file_path: str, error: Exception) -> None:
        self.node_id = node_id
        self._file_path = file_path
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
        raise NotImplementedError("Video seeking is reserved for a later phase")

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        return SourceStatus(
            node_id=self.node_id,
            state=self._state,
            file_path=self._file_path,
            dropped_before_processing=dropped_before_processing,
            warnings=1,
            last_error=self._error,
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
    ) -> None:
        self._lock = threading.RLock()
        self._timing_lock = threading.Lock()
        self._node_times_ns: deque[int] = deque(maxlen=20_000)
        self._facade = EngineFacade(registry, timing_hook=self._record_node_time)
        self._video_source_factory = video_source_factory or VideoSourceService
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
                worker = LatestFrameGraphWorker(self._facade, preview_broker=self._preview_broker)
            old_plan = self._facade.active_plan
            reusable_sources = self._reusable_source_ids(old_plan, plan, reset_reason)
            sources: dict[UUID, SourceController] = {}
            try:
                for node in plan.nodes:
                    if node.definition.type_id != LOAD_VIDEO_TYPE_ID:
                        continue
                    existing = self._sources.get(node.node_id)
                    sources[node.node_id] = (
                        existing
                        if existing is not None and node.node_id in reusable_sources
                        else self._create_video_source(node, worker)
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
                    if old_states[node_id] is SourceState.PLAYING:
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
            with self._timing_lock:
                self._node_times_ns.clear()
            for node_id, source in previous_sources.items():
                if node_id not in reusable_sources:
                    with suppress(Exception):
                        source.close()
            if previous_state is EngineState.RUNNING:
                for node_id, source in sources.items():
                    if old_states.get(node_id) is SourceState.PLAYING:
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
            self._state = EngineState.STOPPED

    def reload(self, source_node_id: UUID | None = None) -> None:
        with self._lock:
            sources = self._selected_sources(source_node_id)
            for source in sources:
                source.reload()
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
            return tuple(
                source.status(
                    dropped_before_processing=(
                        worker.dropped_for(source.node_id) if worker is not None else 0
                    )
                )
                for source in self._selected_sources(source_node_id)
            )

    def metrics(self) -> EngineMetrics:
        with self._lock:
            if self._state is EngineState.CLOSED:
                return EngineMetrics(state=EngineState.CLOSED, graph_revision=self._graph_revision)
            worker = self._worker
            statuses = self.source_status()
            state = self._effective_state(statuses, worker)
            processed_ticks = worker.processed_ticks if worker is not None else 0
            elapsed_s = worker.elapsed_s if worker is not None else 0.0
            processed_fps = processed_ticks / elapsed_s if elapsed_s > 0.0 else 0.0
            with self._timing_lock:
                timings = tuple(self._node_times_ns)
            p95_ms = (
                float(np.percentile(np.asarray(timings, dtype=np.float64), 95)) / 1_000_000
                if timings
                else 0.0
            )
            return EngineMetrics(
                state=state,
                graph_revision=self._graph_revision,
                processed_ticks=processed_ticks,
                processed_fps=processed_fps,
                p95_node_time_ms=p95_ms,
                dropped_before_processing=(worker.total_dropped() if worker is not None else 0),
                skipped_by_selection=sum(status.skipped_by_selection for status in statuses),
                memory_bytes=psutil.Process().memory_info().rss,
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
            return _FailedVideoSource(node.node_id, file_path, error)

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
            if node.definition.type_id == LOAD_VIDEO_TYPE_ID
        }
        return frozenset(
            node.node_id
            for node in new_plan.nodes
            if node.definition.type_id == LOAD_VIDEO_TYPE_ID
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

    def _record_node_time(self, node_id: UUID, elapsed_ns: int) -> None:
        del node_id
        with self._timing_lock:
            self._node_times_ns.append(elapsed_ns)

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


def _bool_parameter(node: CompiledNode, parameter_id: str) -> bool:
    value = node.parameters[parameter_id]
    if not isinstance(value, bool):
        raise TypeError(f"Expected boolean parameter {parameter_id!r}")
    return value


__all__ = [
    "InProcessEngineClient",
    "LatestFrameGraphWorker",
    "SourceController",
    "VideoSourceFactory",
]
