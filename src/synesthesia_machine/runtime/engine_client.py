"""Supervised process-backed implementation of the UI-facing EngineClient."""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from multiprocessing import get_context
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Protocol, cast, get_args
from uuid import UUID, uuid4

from synesthesia_machine.contracts.engine_client import (
    DeviceCatalogue,
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
    ResetReason,
    SourceStatus,
    ValuePreview,
)
from synesthesia_machine.contracts.engine_messages import (
    ENGINE_PROTOCOL_VERSION,
    ActivateGraph,
    CommandAcknowledged,
    CommandFailed,
    ConfigurePreviewSlot,
    DeviceCatalogueResponse,
    EngineAsyncEvent,
    EngineCommand,
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
    PreviewFormatChanged,
    PreviewSlotConfigured,
    ProtocolMismatch,
    QueryDeviceCatalogue,
    QueryMetrics,
    QueryMidiOutputStatus,
    QueryNodeMemoryDiagnostics,
    QueryNodeProfiles,
    QuerySourceStatus,
    ResetProfiling,
    SetProfilingEnabled,
    Shutdown,
    ShutdownAcknowledged,
    SourceStatusResponse,
    TransportAction,
    TransportCommand,
    ValuePreviewsPublished,
    WaitUntilIdle,
)
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.runtime.engine_server import engine_server_main
from synesthesia_machine.runtime.graph_payload import snapshot_to_payload
from synesthesia_machine.runtime.shared_previews import OwnedPreviewSlot

DEFAULT_REQUEST_TIMEOUT_S = 3.0
DEFAULT_ACTIVATION_TIMEOUT_S = 10.0
DEFAULT_HEARTBEAT_TIMEOUT_S = 2.0
DEFAULT_CLOSE_TIMEOUT_S = 2.0

_WINDOWS_EXIT_CODES = {
    0xC0000005: "Windows access violation in native code",
    0xC000001D: "Windows illegal-instruction fault in native code",
    0xC0000094: "Windows integer divide-by-zero fault",
    0xC0000409: "Windows stack-buffer overrun or fast-fail termination",
}


def _exit_code_description(exit_code: int | None) -> str:
    if exit_code is None:
        return "Engine process disconnected before an exit code was available"
    unsigned = exit_code & 0xFFFFFFFF
    native_detail = _WINDOWS_EXIT_CODES.get(unsigned)
    if native_detail is not None:
        return f"Engine process exited with code {exit_code} (0x{unsigned:08X}: {native_detail})"
    return f"Engine process exited with code {exit_code}"


class DuplexConnection(Protocol):
    def send(self, obj: object) -> None: ...

    def recv(self) -> object: ...

    def close(self) -> None: ...


class EventQueueReader(Protocol):
    def get(self, block: bool = True, timeout: float | None = None) -> object: ...

    def close(self) -> None: ...

    def join_thread(self) -> None: ...


class EngineProtocolError(RuntimeError):
    """Raised when parent and child do not agree on the IPC contract version."""


@dataclass(slots=True)
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    response: EngineResponse | None = None
    error: BaseException | None = None


# Keep runtime validation sourced from the protocol unions. A duplicated allow-list can otherwise
# reject a newly added, valid response and incorrectly mark the engine as crashed.
_RESPONSE_TYPES: tuple[type[object], ...] = get_args(EngineResponse)
_ASYNC_EVENT_TYPES: tuple[type[object], ...] = get_args(EngineAsyncEvent)


class ProcessEngineClient:
    """Own a spawned engine, correlate bounded requests, and survive child failure."""

    def __init__(
        self,
        *,
        auto_start: bool = True,
        protocol_version: int = ENGINE_PROTOCOL_VERSION,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        activation_timeout_s: float = DEFAULT_ACTIVATION_TIMEOUT_S,
        heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        close_timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S,
        crash_log_path: str | Path | None = None,
    ) -> None:
        self._context: SpawnContext = get_context("spawn")
        self._protocol_version = protocol_version
        self._request_timeout_s = request_timeout_s
        self._activation_timeout_s = activation_timeout_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._close_timeout_s = close_timeout_s
        self._crash_log_path = (
            None if crash_log_path is None else str(Path(crash_log_path).resolve())
        )
        self._engine_log_directory = (
            None if self._crash_log_path is None else str(Path(self._crash_log_path).parent)
        )
        self._lifecycle_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._connection: DuplexConnection | None = None
        self._event_queue: EventQueueReader | None = None
        self._process: BaseProcess | None = None
        self._response_thread: threading.Thread | None = None
        self._event_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._connection_state = EngineConnectionState.STARTING
        self._child_process_id: int | None = None
        self._graph_revision: int | None = None
        self._last_heartbeat_monotonic_ns: int | None = None
        self._last_error: str | None = None
        self._last_exit_code: int | None = None
        self._restart_count = 0
        self._latest_valid_snapshot: GraphSnapshot | None = None
        self._latest_demand_roots: tuple[UUID, ...] | None = None
        self._note_previews: dict[UUID, NotePreview] = {}
        self._value_previews: dict[tuple[UUID, str], ValuePreview] = {}
        self._image_slots: dict[tuple[UUID, str], OwnedPreviewSlot] = {}
        self._closed = False
        if auto_start:
            self.start()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Engine client is closed")
            if self._process is not None and self._process.is_alive():
                return
            self._dispose_handles_locked()
            parent_connection, child_connection = self._context.Pipe(duplex=True)
            raw_event_queue = self._context.Queue(maxsize=32)
            event_queue = cast(EventQueueReader, raw_event_queue)
            process = self._context.Process(
                target=engine_server_main,
                args=(
                    child_connection,
                    raw_event_queue,
                    self._crash_log_path,
                    self._engine_log_directory,
                ),
                name="synesthesia-engine",
            )
            self._connection_state = EngineConnectionState.STARTING
            self._child_process_id = None
            self._last_heartbeat_monotonic_ns = None
            self._last_error = None
            self._last_exit_code = None
            self._reader_stop.clear()
            if self._crash_log_path is not None:
                with suppress(OSError):
                    Path(self._crash_log_path).unlink(missing_ok=True)
                with suppress(OSError):
                    Path(f"{self._crash_log_path}.pending").unlink(missing_ok=True)
            try:
                process.start()
            except Exception:
                parent_connection.close()
                child_connection.close()
                event_queue.close()
                event_queue.join_thread()
                process.close()
                raise
            child_connection.close()
            self._connection = cast(DuplexConnection, parent_connection)
            self._event_queue = event_queue
            self._process = process
            self._start_readers_locked()

        try:
            response = self._request(
                Handshake(
                    uuid4().hex,
                    os.getpid(),
                    protocol_version=self._protocol_version,
                ),
                HandshakeAcknowledged,
            )
        except Exception:
            self._stop_current_process(graceful=False)
            raise
        with self._lifecycle_lock:
            self._child_process_id = response.child_process_id
            self._connection_state = EngineConnectionState.CONNECTED

    def activate(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
    ) -> EngineActivation:
        roots = None if demand_roots is None else tuple(sorted(demand_roots, key=str))
        return self._activate(snapshot, roots, ResetReason.PLAN_REPLACED)

    def play(self, source_node_id: UUID | None = None) -> None:
        self._transport(TransportAction.PLAY, source_node_id)

    def pause(self, source_node_id: UUID | None = None) -> None:
        self._transport(TransportAction.PAUSE, source_node_id)

    def resume(self, source_node_id: UUID | None = None) -> None:
        self._transport(TransportAction.RESUME, source_node_id)

    def stop(self, source_node_id: UUID | None = None) -> None:
        self._transport(TransportAction.STOP, source_node_id)

    def reload(self, source_node_id: UUID | None = None) -> None:
        self._transport(TransportAction.RELOAD, source_node_id)

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        command = TransportCommand(
            uuid4().hex,
            TransportAction.SEEK,
            self._graph_revision,
            source_node_id,
            source_time_s,
        )
        self._request(command, CommandAcknowledged)

    def panic(self) -> None:
        self._request(Panic(uuid4().hex, self._graph_revision), CommandAcknowledged)

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        response = self._request(
            QuerySourceStatus(uuid4().hex, self._graph_revision, source_node_id),
            SourceStatusResponse,
        )
        self._accept_revision(response.graph_revision)
        return response.statuses

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        response = self._request(
            QueryMidiOutputStatus(uuid4().hex, self._graph_revision, output_node_id),
            MidiOutputStatusResponse,
        )
        self._accept_revision(response.graph_revision)
        return response.statuses

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        response = self._request(
            QueryDeviceCatalogue(uuid4().hex, force_refresh),
            DeviceCatalogueResponse,
        )
        return response.catalogue

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        response = self._request(
            QueryNodeMemoryDiagnostics(uuid4().hex, self._graph_revision, node_id),
            NodeMemoryDiagnosticsResponse,
        )
        self._accept_revision(response.graph_revision)
        return response.diagnostics

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        response = self._request(
            QueryNodeProfiles(uuid4().hex, self._graph_revision),
            NodeProfilesResponse,
        )
        self._accept_revision(response.graph_revision)
        return response.profiles

    def reset_profiling(self) -> None:
        self._request(
            ResetProfiling(uuid4().hex, self._graph_revision),
            CommandAcknowledged,
        )

    def set_profiling_enabled(self, enabled: bool) -> None:
        self._request(
            SetProfilingEnabled(uuid4().hex, self._graph_revision, enabled),
            CommandAcknowledged,
        )

    def metrics(self) -> EngineMetrics:
        status = self.status()
        if status.connection_state in {
            EngineConnectionState.CRASHED,
            EngineConnectionState.UNRESPONSIVE,
        }:
            return EngineMetrics(
                state=EngineState.ERROR,
                graph_revision=self._graph_revision,
                restart_count=self._restart_count,
                child_process_id=self._child_process_id,
                heartbeat_age_s=self._heartbeat_age_s(),
            )
        response = self._request(
            QueryMetrics(uuid4().hex, self._graph_revision),
            MetricsResponse,
        )
        self._accept_revision(response.graph_revision)
        return replace(
            response.metrics,
            restart_count=self._restart_count,
            heartbeat_age_s=self._heartbeat_age_s(),
        )

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        # The UI polls at display cadence, so the cheap seqlock-header peek runs
        # for every slot and the full frame copy only for slots that advanced
        # past the caller's threshold. Slots idle at their last frame cost a
        # 28-byte header read per tick instead of a full preview memcpy.
        thresholds = after_sequences or {}
        previews: list[ImagePreview] = []
        with self._preview_lock:
            for key, slot in sorted(
                self._image_slots.items(), key=lambda item: (str(item[0][0]), item[0][1])
            ):
                threshold = thresholds.get(key, 0)
                if slot.peek_sequence() <= threshold:
                    continue
                preview = slot.read()
                if preview is not None and preview.sequence > threshold:
                    previews.append(preview)
        return tuple(previews)

    def preview_shared_memory_names(self) -> tuple[str, ...]:
        """Expose owned slot names for lifecycle diagnostics and cleanup tests."""

        with self._preview_lock:
            ordered_slots = sorted(
                self._image_slots.items(), key=lambda item: (str(item[0][0]), item[0][1])
            )
            return tuple(slot.name for _, slot in ordered_slots)

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        thresholds = after_sequences or {}
        with self._preview_lock:
            return tuple(
                preview
                for node_id, preview in sorted(
                    self._note_previews.items(), key=lambda item: str(item[0])
                )
                if preview.sequence > thresholds.get(node_id, 0)
            )

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        thresholds = after_sequences or {}
        with self._preview_lock:
            return tuple(
                preview
                for key, preview in sorted(
                    self._value_previews.items(),
                    key=lambda item: (str(item[0][0]), item[0][1]),
                )
                if preview.sequence > thresholds.get(key, 0)
            )

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        response = self._request(
            WaitUntilIdle(uuid4().hex, self._graph_revision, timeout_s),
            IdleResponse,
            timeout_s=max(self._request_timeout_s, timeout_s + 0.5),
        )
        return response.idle

    def status(self) -> EngineStatus:
        with self._lifecycle_lock:
            self._refresh_liveness_locked()
            return EngineStatus(
                self._connection_state,
                self._child_process_id,
                self._graph_revision,
                self._last_heartbeat_monotonic_ns,
                self._restart_count,
                self._process.exitcode if self._process is not None else self._last_exit_code,
                self._last_error,
                self._crash_log_path,
            )

    def restart(self) -> EngineActivation | None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Engine client is closed")
            snapshot = self._latest_valid_snapshot
            demand_roots = self._latest_demand_roots
            self._restart_count += 1
            self._connection_state = EngineConnectionState.RESTARTING
        self._stop_current_process(graceful=True)
        self.start()
        if snapshot is None:
            return None
        return self._activate(snapshot, demand_roots, ResetReason.ENGINE_RESTARTED)

    def force_terminate(self) -> None:
        """Terminate the child for crash-recovery diagnostics and process tests."""

        self._stop_current_process(graceful=False, mark_crashed=True)

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
        self._stop_current_process(graceful=True)
        with self._lifecycle_lock:
            self._closed = True
            self._connection_state = EngineConnectionState.CLOSED

    def _activate(
        self,
        snapshot: GraphSnapshot,
        demand_roots: tuple[UUID, ...] | None,
        reset_reason: ResetReason,
    ) -> EngineActivation:
        response = self._request(
            ActivateGraph(
                uuid4().hex,
                snapshot.revision,
                snapshot_to_payload(snapshot),
                demand_roots,
                reset_reason,
            ),
            GraphActivationAcknowledged,
            timeout_s=self._activation_timeout_s,
        )
        activation = response.activation
        if activation.activated:
            with self._preview_lock:
                self._note_previews.clear()
                self._value_previews.clear()
                self._close_image_slots_locked()
            with self._lifecycle_lock:
                self._graph_revision = activation.graph_revision
                self._latest_valid_snapshot = snapshot
                self._latest_demand_roots = demand_roots
        return activation

    def _transport(self, action: TransportAction, source_node_id: UUID | None) -> None:
        self._request(
            TransportCommand(
                uuid4().hex,
                action,
                self._graph_revision,
                source_node_id,
            ),
            CommandAcknowledged,
        )

    def _request[ResponseT: EngineResponse](
        self,
        command: EngineCommand,
        response_type: type[ResponseT],
        *,
        timeout_s: float | None = None,
    ) -> ResponseT:
        pending = _PendingRequest()
        with self._lifecycle_lock:
            self._refresh_liveness_locked()
            connection = self._connection
            if self._closed or self._connection_state is EngineConnectionState.CLOSED:
                raise RuntimeError("Engine client is closed")
            if connection is None or self._connection_state is EngineConnectionState.CRASHED:
                raise RuntimeError(self._last_error or "Engine process is not connected")
        with self._pending_lock:
            self._pending[command.request_id] = pending
        try:
            with self._send_lock:
                connection.send(command)
        except (BrokenPipeError, EOFError, OSError) as error:
            with self._pending_lock:
                self._pending.pop(command.request_id, None)
            self._mark_disconnected(f"Engine command send failed: {error}")
            raise RuntimeError("Engine process disconnected") from error

        wait_timeout = self._request_timeout_s if timeout_s is None else timeout_s
        if not pending.event.wait(max(0.0, wait_timeout)):
            with self._pending_lock:
                self._pending.pop(command.request_id, None)
            with self._lifecycle_lock:
                self._connection_state = EngineConnectionState.UNRESPONSIVE
                self._last_error = f"Timed out waiting for {type(command).__name__}"
            raise TimeoutError(self._last_error)
        if pending.error is not None:
            raise RuntimeError(str(pending.error)) from pending.error
        response = pending.response
        if isinstance(response, ProtocolMismatch):
            raise EngineProtocolError(
                f"Engine protocol mismatch: expected {response.expected_version}, "
                f"received {response.received_version}"
            )
        if isinstance(response, CommandFailed):
            self._raise_remote_error(response)
        if not isinstance(response, response_type):
            raise TypeError(
                f"Expected {response_type.__name__}, got "
                f"{type(response).__name__ if response is not None else 'no response'}"
            )
        return response

    def _start_readers_locked(self) -> None:
        self._response_thread = threading.Thread(
            target=self._response_loop,
            name="engine-response-reader",
            daemon=True,
        )
        self._event_thread = threading.Thread(
            target=self._event_loop,
            name="engine-event-reader",
            daemon=True,
        )
        self._response_thread.start()
        self._event_thread.start()

    def _response_loop(self) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            while not self._reader_stop.is_set():
                raw_response = connection.recv()
                if not isinstance(raw_response, _RESPONSE_TYPES):
                    self._mark_disconnected(
                        f"Unsupported engine response: {type(raw_response).__name__}"
                    )
                    return
                response = cast(EngineResponse, raw_response)
                if response.protocol_version != ENGINE_PROTOCOL_VERSION:
                    self._mark_disconnected(
                        f"Unsupported engine response protocol {response.protocol_version}"
                    )
                    return
                with self._pending_lock:
                    pending = self._pending.pop(response.request_id, None)
                if pending is not None:
                    pending.response = response
                    pending.event.set()
        except (EOFError, OSError) as error:
            if not self._reader_stop.is_set():
                self._mark_disconnected(f"Engine response channel closed: {error}")

    def _event_loop(self) -> None:
        event_queue = self._event_queue
        if event_queue is None:
            return
        while not self._reader_stop.is_set():
            try:
                raw_event = event_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return
            if not isinstance(raw_event, _ASYNC_EVENT_TYPES):
                continue
            event = cast(EngineAsyncEvent, raw_event)
            if event.protocol_version != ENGINE_PROTOCOL_VERSION:
                continue
            try:
                self._handle_async_event(event)
            except Exception as error:
                with self._lifecycle_lock:
                    self._last_error = (
                        f"Could not handle {type(event).__name__}: {type(error).__name__}: {error}"
                    )

    def _handle_async_event(self, event: EngineAsyncEvent) -> None:
        if isinstance(event, Heartbeat):
            with self._lifecycle_lock:
                self._last_heartbeat_monotonic_ns = time.monotonic_ns()
                self._child_process_id = event.child_process_id
                if self._connection_state is EngineConnectionState.UNRESPONSIVE:
                    self._connection_state = EngineConnectionState.CONNECTED
            return
        if event.graph_revision != self._graph_revision:
            return
        if isinstance(event, PreviewFormatChanged):
            self._configure_preview_slot(event)
            return
        if isinstance(event, NotePreviewsPublished):
            with self._preview_lock:
                for preview in event.previews:
                    previous = self._note_previews.get(preview.owner_id)
                    if previous is None or preview.sequence > previous.sequence:
                        self._note_previews[preview.owner_id] = preview
            return
        if isinstance(event, ValuePreviewsPublished):
            with self._preview_lock:
                for preview in event.previews:
                    key = (preview.owner_id, preview.source_port_id)
                    previous = self._value_previews.get(key)
                    if previous is None or preview.sequence > previous.sequence:
                        self._value_previews[key] = preview
            return
        with self._lifecycle_lock:
            self._last_error = event.message
            if event.fatal:
                self._connection_state = EngineConnectionState.CRASHED

    def _configure_preview_slot(self, event: PreviewFormatChanged) -> None:
        slot_key = (event.owner_id, event.source_port_id)
        with self._preview_lock:
            current = self._image_slots.get(slot_key)
            if current is not None and (
                current.descriptor.generation == event.generation
                and current.descriptor.width == event.width
                and current.descriptor.height == event.height
                and current.descriptor.channels == event.channels
            ):
                return
        replacement = OwnedPreviewSlot.create(
            owner_id=event.owner_id,
            source_port_id=event.source_port_id,
            generation=event.generation,
            width=event.width,
            height=event.height,
            channels=event.channels,
        )
        try:
            self._request(
                ConfigurePreviewSlot(
                    uuid4().hex,
                    event.graph_revision,
                    replacement.descriptor,
                ),
                PreviewSlotConfigured,
            )
        except Exception as error:
            replacement.close()
            with self._lifecycle_lock:
                self._last_error = (
                    f"Could not configure preview slot for {event.owner_id}: "
                    f"{type(error).__name__}: {error}"
                )
            return
        with self._lifecycle_lock:
            stale = self._closed or event.graph_revision != self._graph_revision
        if stale:
            replacement.close()
            return
        with self._preview_lock:
            previous = self._image_slots.get(slot_key)
            self._image_slots[slot_key] = replacement
        if previous is not None:
            previous.close()

    def _stop_current_process(
        self,
        *,
        graceful: bool,
        mark_crashed: bool = False,
    ) -> None:
        with self._lifecycle_lock:
            process = self._process
            connection = self._connection
        if process is None:
            return
        if graceful and process.is_alive() and connection is not None:
            with suppress(RuntimeError, TimeoutError, BrokenPipeError, EOFError, OSError):
                self._request(
                    Shutdown(uuid4().hex, self._graph_revision),
                    ShutdownAcknowledged,
                    timeout_s=self._close_timeout_s,
                )
        self._reader_stop.set()
        process.join(self._close_timeout_s)
        if process.is_alive():
            process.terminate()
            process.join(self._close_timeout_s)
        if process.is_alive():
            process.kill()
            process.join(self._close_timeout_s)
        if process.is_alive():
            raise TimeoutError(f"Engine process {process.pid} did not terminate")
        with self._preview_lock:
            self._close_image_slots_locked()
        with self._lifecycle_lock:
            self._last_exit_code = process.exitcode
            if mark_crashed and not self._closed:
                self._connection_state = EngineConnectionState.CRASHED
                self._record_crash_exit_locked(process.exitcode)
            self._dispose_handles_locked()

    def _record_crash_exit_locked(self, exit_code: int | None) -> None:
        self._last_exit_code = exit_code
        self._last_error = _exit_code_description(exit_code)
        if self._crash_log_path is None:
            return
        path = Path(self._crash_log_path)
        try:
            pending = Path(f"{path}.pending")
            if pending.is_file() and pending.stat().st_size > 0:
                pending.replace(path)
            if path.is_file() and path.stat().st_size > 0:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            unsigned = None if exit_code is None else exit_code & 0xFFFFFFFF
            hexadecimal = "unavailable" if unsigned is None else f"0x{unsigned:08X}"
            path.write_text(
                "Synesthesia Machine engine crash report\n"
                "Generated by the parent process supervisor because the child did not write "
                "a Python traceback.\n\n"
                f"{self._last_error}\n"
                f"Decimal exit code: {exit_code}\n"
                f"Hexadecimal exit code: {hexadecimal}\n\n"
                "A missing Python traceback usually means the process stopped inside a native "
                "dependency or was forcibly terminated.\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def _dispose_handles_locked(self) -> None:
        self._reader_stop.set()
        connection = self._connection
        event_queue = self._event_queue
        process = self._process
        response_thread = self._response_thread
        event_thread = self._event_thread
        self._connection = None
        self._event_queue = None
        self._process = None
        self._response_thread = None
        self._event_thread = None
        if connection is not None:
            with suppress(OSError):
                connection.close()
        if event_queue is not None:
            with suppress(OSError, ValueError):
                event_queue.close()
            with suppress(OSError, ValueError):
                event_queue.join_thread()
        current = threading.current_thread()
        for thread in (response_thread, event_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=0.5)
        if process is not None and not process.is_alive():
            self._last_exit_code = process.exitcode
            process.close()
        self._fail_pending_locked(RuntimeError("Engine process disconnected"))

    def _mark_disconnected(self, message: str) -> None:
        with self._lifecycle_lock:
            if self._closed or self._reader_stop.is_set():
                return
            self._connection_state = EngineConnectionState.CRASHED
            self._last_error = message
        with self._pending_lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
        for request in pending:
            request.error = RuntimeError(message)
            request.event.set()

    def _fail_pending_locked(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
        for request in pending:
            request.error = error
            request.event.set()

    def _close_image_slots_locked(self) -> None:
        slots = tuple(self._image_slots.values())
        self._image_slots.clear()
        for slot in slots:
            slot.close()

    def _refresh_liveness_locked(self) -> None:
        process = self._process
        if (
            process is not None
            and not process.is_alive()
            and not self._closed
            and self._connection_state is not EngineConnectionState.RESTARTING
        ):
            self._connection_state = EngineConnectionState.CRASHED
            self._record_crash_exit_locked(process.exitcode)
        if (
            self._connection_state is EngineConnectionState.CONNECTED
            and self._last_heartbeat_monotonic_ns is not None
            and self._heartbeat_age_s() > self._heartbeat_timeout_s
        ):
            self._connection_state = EngineConnectionState.UNRESPONSIVE
            self._last_error = "Engine heartbeat timed out"

    def _heartbeat_age_s(self) -> float:
        heartbeat = self._last_heartbeat_monotonic_ns
        if heartbeat is None:
            return 0.0
        return max(0.0, (time.monotonic_ns() - heartbeat) / 1_000_000_000)

    def _accept_revision(self, revision: int | None) -> None:
        if (
            revision is not None
            and self._graph_revision is not None
            and revision < self._graph_revision
        ):
            raise RuntimeError(
                f"Stale engine response for graph revision {revision}; "
                f"active revision is {self._graph_revision}"
            )

    @staticmethod
    def _raise_remote_error(response: CommandFailed) -> None:
        message = response.message or response.error_type
        if response.error_type == "KeyError":
            raise KeyError(message)
        if response.error_type == "NotImplementedError":
            raise NotImplementedError(message)
        if response.error_type == "ValueError":
            raise ValueError(message)
        raise RuntimeError(message)


__all__ = ["EngineProtocolError", "ProcessEngineClient"]
