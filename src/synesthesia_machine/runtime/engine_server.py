"""Spawn-safe production engine child and command dispatcher."""

from __future__ import annotations

import faulthandler
import os
import queue
import threading
import time
import traceback
from contextlib import suppress
from dataclasses import replace
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from typing import Protocol
from uuid import UUID

import cv2
import numpy as np
import psutil

from synesthesia_machine.contracts.engine_client import ImagePreview
from synesthesia_machine.contracts.engine_messages import (
    ENGINE_PROTOCOL_VERSION,
    ActivateGraph,
    CommandAcknowledged,
    CommandFailed,
    ConfigurePreviewSlot,
    EngineAsyncEvent,
    EngineCommand,
    EngineErrorPublished,
    EngineResponse,
    GraphActivationAcknowledged,
    Handshake,
    HandshakeAcknowledged,
    Heartbeat,
    IdleResponse,
    MetricsResponse,
    MidiOutputStatusResponse,
    NodeMemoryDiagnosticsResponse,
    NodeProfilesResponse,
    NotePreviewsPublished,
    Panic,
    Ping,
    Pong,
    PreviewFormatChanged,
    PreviewSlotConfigured,
    PreviewSlotDescriptor,
    ProtocolMismatch,
    QueryMetrics,
    QueryMidiOutputStatus,
    QueryNodeMemoryDiagnostics,
    QueryNodeProfiles,
    QuerySourceStatus,
    ResetProfiling,
    SetProfilingEnabled,
    SharedFrameReady,
    Shutdown,
    ShutdownAcknowledged,
    SourceStatusResponse,
    TransportAction,
    TransportCommand,
    WaitUntilIdle,
    WriteSharedFrame,
)
from synesthesia_machine.nodes.composition import create_builtin_registry
from synesthesia_machine.runtime.in_process_engine import InProcessEngineClient
from synesthesia_machine.runtime.shared_previews import AttachedPreviewSlot

HEARTBEAT_INTERVAL_S = 0.25
PREVIEW_POLL_INTERVAL_S = 1.0 / 60.0
MAX_OPENCV_THREADS = 16


def configure_opencv_threads() -> int:
    """Bound native parallelism so OpenCV does not oversubscribe the engine process."""

    available = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1
    thread_count = max(1, min(MAX_OPENCV_THREADS, available))
    cv2.setNumThreads(thread_count)
    return thread_count


class DuplexConnection(Protocol):
    def send(self, obj: object) -> None: ...

    def recv(self) -> object: ...

    def poll(self, timeout: float = 0.0) -> bool: ...

    def close(self) -> None: ...


class EventQueueWriter(Protocol):
    def put_nowait(self, obj: object) -> None: ...

    def close(self) -> None: ...


_COMMAND_TYPES = (
    Ping,
    WriteSharedFrame,
    Handshake,
    ActivateGraph,
    TransportCommand,
    Panic,
    QuerySourceStatus,
    QueryMidiOutputStatus,
    QueryNodeMemoryDiagnostics,
    QueryNodeProfiles,
    ResetProfiling,
    SetProfilingEnabled,
    QueryMetrics,
    WaitUntilIdle,
    ConfigurePreviewSlot,
    Shutdown,
)


class _EventPublisher:
    """Publish heartbeats/compact previews without blocking command dispatch or graph work."""

    def __init__(
        self,
        engine: InProcessEngineClient,
        event_queue: EventQueueWriter,
        *,
        started_monotonic_ns: int,
        heartbeat_interval_s: float,
    ) -> None:
        self._engine = engine
        self._event_queue = event_queue
        self._started_monotonic_ns = started_monotonic_ns
        self._heartbeat_interval_s = heartbeat_interval_s
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._graph_revision: int | None = None
        self._heartbeat_sequence = 0
        self._image_sequences: dict[UUID, int] = {}
        self._note_sequences: dict[UUID, int] = {}
        self._format_generations: dict[UUID, int] = {}
        self._announced_formats: dict[UUID, tuple[int, int, int, int]] = {}
        self._slots: dict[UUID, AttachedPreviewSlot] = {}
        self._thread = threading.Thread(
            target=self._run,
            name="engine-event-publisher",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def graph_activated(self, graph_revision: int) -> None:
        with self._lock:
            self._graph_revision = graph_revision
            self._image_sequences.clear()
            self._note_sequences.clear()
            self._announced_formats.clear()
            self._close_slots_locked()

    def configure_slot(self, descriptor: PreviewSlotDescriptor, graph_revision: int) -> None:
        replacement = AttachedPreviewSlot(descriptor)
        with self._lock:
            if graph_revision != self._graph_revision:
                replacement.close()
                raise ValueError(
                    f"Preview slot revision {graph_revision} is stale; "
                    f"active revision is {self._graph_revision}"
                )
            expected = self._announced_formats.get(descriptor.node_id)
            configured = (
                descriptor.generation,
                descriptor.width,
                descriptor.height,
                descriptor.channels,
            )
            if expected != configured:
                replacement.close()
                raise ValueError("Preview slot does not match the announced format")
            previous = self._slots.get(descriptor.node_id)
            self._slots[descriptor.node_id] = replacement
        if previous is not None:
            previous.close()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        with self._lock:
            self._close_slots_locked()

    def _run(self) -> None:
        next_heartbeat = 0.0
        while not self._stop.wait(PREVIEW_POLL_INTERVAL_S):
            now = time.monotonic()
            if now >= next_heartbeat:
                self._publish_heartbeat()
                next_heartbeat = now + self._heartbeat_interval_s
            try:
                self._publish_previews()
            except Exception as error:
                with self._lock:
                    graph_revision = self._graph_revision
                self._put_event(
                    EngineErrorPublished(
                        graph_revision,
                        f"Preview publisher failed: {type(error).__name__}: {error}",
                        traceback.format_exc(),
                    )
                )

    def _publish_heartbeat(self) -> None:
        with self._lock:
            self._heartbeat_sequence += 1
            event: EngineAsyncEvent = Heartbeat(
                self._heartbeat_sequence,
                os.getpid(),
                time.monotonic_ns(),
                self._started_monotonic_ns,
                self._graph_revision,
            )
        self._put_event(event)

    def _publish_previews(self) -> None:
        with self._lock:
            graph_revision = self._graph_revision
            image_sequences = dict(self._image_sequences)
            note_sequences = dict(self._note_sequences)
        if graph_revision is None:
            return
        for preview in self._engine.poll_image_previews(image_sequences):
            with self._lock:
                if graph_revision != self._graph_revision:
                    return
                slot = self._slots.get(preview.node_id)
                if slot is None or (
                    slot.descriptor.width != preview.width
                    or slot.descriptor.height != preview.height
                    or slot.descriptor.channels != preview.channels
                ):
                    self._announce_format_locked(graph_revision, preview)
                    continue
                try:
                    slot.write(preview)
                except (BufferError, OSError, RuntimeError, ValueError):
                    slot.close()
                    self._slots.pop(preview.node_id, None)
                    self._announce_format_locked(graph_revision, preview)
                    continue
                self._image_sequences[preview.node_id] = preview.sequence
        note_previews = self._engine.poll_note_previews(note_sequences)
        if note_previews and self._put_event(NotePreviewsPublished(graph_revision, note_previews)):
            with self._lock:
                if graph_revision == self._graph_revision:
                    for preview in note_previews:
                        self._note_sequences[preview.node_id] = preview.sequence

    def _announce_format_locked(self, graph_revision: int, preview: ImagePreview) -> None:
        current = self._announced_formats.get(preview.node_id)
        dimensions = (preview.width, preview.height, preview.channels)
        if current is None or current[1:] != dimensions:
            generation = self._format_generations.get(preview.node_id, 0) + 1
            self._format_generations[preview.node_id] = generation
            announced = (generation, *dimensions)
            self._announced_formats[preview.node_id] = announced
            previous = self._slots.pop(preview.node_id, None)
            if previous is not None:
                previous.close()
        else:
            generation = current[0]
        self._put_event(
            PreviewFormatChanged(
                graph_revision,
                preview.node_id,
                generation,
                preview.width,
                preview.height,
                preview.channels,
            )
        )

    def _put_event(self, event: EngineAsyncEvent) -> bool:
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            return False
        return True

    def _close_slots_locked(self) -> None:
        slots = tuple(self._slots.values())
        self._slots.clear()
        for slot in slots:
            slot.close()


class EngineServer:
    """Own all runtime resources and dispatch trusted commands in the child."""

    def __init__(
        self,
        connection: DuplexConnection,
        event_queue: EventQueueWriter,
        *,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        self._connection = connection
        self._event_queue = event_queue
        self._heartbeat_interval_s = heartbeat_interval_s
        self._started_monotonic_ns = time.monotonic_ns()
        self._graph_revision: int | None = None
        self._engine = InProcessEngineClient(create_builtin_registry())
        self._process = psutil.Process()
        self._events = _EventPublisher(
            self._engine,
            event_queue,
            started_monotonic_ns=self._started_monotonic_ns,
            heartbeat_interval_s=heartbeat_interval_s,
        )

    def run(self) -> None:
        self._events.start()
        try:
            while True:
                if not self._connection.poll(0.1):
                    continue
                try:
                    raw_command = self._connection.recv()
                except EOFError:
                    return
                if not isinstance(raw_command, _COMMAND_TYPES):
                    self._send_failure(
                        "",
                        TypeError(f"Unsupported engine command: {type(raw_command).__name__}"),
                    )
                    continue
                command: EngineCommand = raw_command
                if command.protocol_version != ENGINE_PROTOCOL_VERSION:
                    self._connection.send(
                        ProtocolMismatch(
                            command.request_id,
                            ENGINE_PROTOCOL_VERSION,
                            command.protocol_version,
                        )
                    )
                    if isinstance(command, Handshake):
                        return
                    continue
                if self._dispatch(command):
                    return
        finally:
            self._events.close()
            with suppress(Exception):
                self._engine.panic()
            self._engine.close()

    def _dispatch(self, command: EngineCommand) -> bool:
        try:
            response = self._handle(command)
        except Exception as error:
            self._send_failure(command.request_id, error)
            return False
        self._connection.send(response)
        return isinstance(command, Shutdown)

    def _handle(self, command: EngineCommand) -> EngineResponse:
        if isinstance(command, Ping):
            return Pong(command.request_id, os.getpid())
        if isinstance(command, WriteSharedFrame):
            return self._write_probe_frame(command)
        if isinstance(command, Handshake):
            return HandshakeAcknowledged(
                command.request_id,
                os.getpid(),
                self._started_monotonic_ns,
            )
        if isinstance(command, ActivateGraph):
            activation = self._engine.activate(
                command.snapshot.to_snapshot(),
                demand_roots=command.demand_roots,
                reset_reason=command.reset_reason,
            )
            if activation.activated:
                self._graph_revision = activation.graph_revision
                self._events.graph_activated(activation.graph_revision)
            return GraphActivationAcknowledged(
                command.request_id,
                command.graph_revision,
                activation,
            )
        if isinstance(command, TransportCommand):
            self._handle_transport(command)
            return CommandAcknowledged(command.request_id, self._graph_revision)
        if isinstance(command, Panic):
            self._engine.panic()
            return CommandAcknowledged(command.request_id, self._graph_revision)
        if isinstance(command, QuerySourceStatus):
            return SourceStatusResponse(
                command.request_id,
                self._graph_revision,
                self._engine.source_status(command.source_node_id),
            )
        if isinstance(command, QueryMidiOutputStatus):
            return MidiOutputStatusResponse(
                command.request_id,
                self._graph_revision,
                self._engine.midi_output_status(command.output_node_id),
            )
        if isinstance(command, QueryNodeMemoryDiagnostics):
            return NodeMemoryDiagnosticsResponse(
                command.request_id,
                self._graph_revision,
                self._engine.node_memory_diagnostics(command.node_id),
            )
        if isinstance(command, QueryNodeProfiles):
            return NodeProfilesResponse(
                command.request_id,
                self._graph_revision,
                self._engine.node_profiles(),
            )
        if isinstance(command, ResetProfiling):
            self._engine.reset_profiling()
            return CommandAcknowledged(command.request_id, self._graph_revision)
        if isinstance(command, SetProfilingEnabled):
            self._engine.set_profiling_enabled(command.enabled)
            return CommandAcknowledged(command.request_id, self._graph_revision)
        if isinstance(command, QueryMetrics):
            metrics = self._engine.metrics()
            metrics = replace(
                metrics,
                child_process_id=os.getpid(),
                uptime_s=(time.monotonic_ns() - self._started_monotonic_ns) / 1_000_000_000,
                cpu_percent=self._process.cpu_percent(),
                system_memory_bytes=psutil.virtual_memory().used,
            )
            return MetricsResponse(command.request_id, self._graph_revision, metrics)
        if isinstance(command, WaitUntilIdle):
            return IdleResponse(
                command.request_id,
                self._graph_revision,
                self._engine.wait_until_idle(command.timeout_s),
            )
        if isinstance(command, ConfigurePreviewSlot):
            self._events.configure_slot(command.descriptor, command.graph_revision)
            return PreviewSlotConfigured(
                command.request_id,
                command.graph_revision,
                command.descriptor.node_id,
                command.descriptor.generation,
            )
        self._engine.panic()
        self._engine.close()
        return ShutdownAcknowledged(command.request_id, self._graph_revision)

    def _handle_transport(self, command: TransportCommand) -> None:
        if command.action is TransportAction.PLAY:
            self._engine.play(command.source_node_id)
        elif command.action is TransportAction.PAUSE:
            self._engine.pause(command.source_node_id)
        elif command.action is TransportAction.RESUME:
            self._engine.resume(command.source_node_id)
        elif command.action is TransportAction.STOP:
            self._engine.stop(command.source_node_id)
        elif command.action is TransportAction.RELOAD:
            self._engine.reload(command.source_node_id)
        elif command.action is TransportAction.SEEK:
            if command.source_node_id is None or command.source_time_s is None:
                raise ValueError("Seek requires a source node and source time")
            self._engine.seek(command.source_node_id, command.source_time_s)

    def _write_probe_frame(self, command: WriteSharedFrame) -> SharedFrameReady:
        shared_memory = SharedMemory(name=command.shared_memory_name)
        try:
            target = np.ndarray(
                (command.height, command.width, 3),
                dtype=np.uint8,
                buffer=shared_memory.buf,
            )
            target[..., 0] = np.arange(command.width, dtype=np.uint8)[np.newaxis, :]
            target[..., 1] = np.arange(command.height, dtype=np.uint8)[:, np.newaxis]
            target[..., 2] = 127
        finally:
            shared_memory.close()
        return SharedFrameReady(command.request_id, 1)

    def _send_failure(self, request_id: str, error: Exception) -> None:
        self._connection.send(
            CommandFailed(
                request_id,
                self._graph_revision,
                type(error).__name__,
                str(error),
                traceback.format_exc(),
            )
        )


def engine_server_main(
    connection: DuplexConnection,
    event_queue: EventQueueWriter,
    crash_log_path: str | None = None,
) -> None:
    """Top-level Windows-spawn target; never imports or creates Qt objects."""

    crash_path = None if crash_log_path is None else Path(crash_log_path)
    crash_capture_path = None if crash_path is None else Path(f"{crash_path}.pending")
    crash_stream = None
    completed_normally = False
    try:
        if crash_capture_path is not None:
            crash_capture_path.parent.mkdir(parents=True, exist_ok=True)
            crash_stream = crash_capture_path.open("w", encoding="utf-8")
            faulthandler.enable(file=crash_stream, all_threads=True)
        configure_opencv_threads()
        EngineServer(connection, event_queue).run()
        completed_normally = True
    except BaseException:
        detail = traceback.format_exc()
        if crash_stream is not None:
            with suppress(OSError):
                crash_stream.write(detail)
                crash_stream.flush()
        elif crash_path is not None:
            _write_crash_log(crash_path, detail)
        raise
    finally:
        if crash_stream is not None:
            with suppress(RuntimeError):
                faulthandler.disable()
            with suppress(OSError):
                crash_stream.close()
            if completed_normally and crash_capture_path is not None:
                with suppress(OSError):
                    crash_capture_path.unlink(missing_ok=True)
            elif crash_path is not None and crash_capture_path is not None:
                with suppress(OSError):
                    crash_capture_path.replace(crash_path)
        connection.close()
        event_queue.close()


def _write_crash_log(path: Path, detail: str) -> None:
    """Persist one child-failure traceback without depending on UI logging state."""

    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(detail, encoding="utf-8")


__all__ = [
    "MAX_OPENCV_THREADS",
    "EngineServer",
    "configure_opencv_threads",
    "engine_server_main",
]
