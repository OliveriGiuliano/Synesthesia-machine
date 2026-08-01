"""Client-facing engine control, status, metrics, and preview contracts.

These values intentionally describe the UI-side API rather than the Phase 3
in-process implementation.  A later process-backed client can implement the
same protocol without changing transport or widget code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot
    from synesthesia_machine.graph.validation import ValidationReport


class EngineState(StrEnum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


class EngineConnectionState(StrEnum):
    STARTING = "STARTING"
    CONNECTED = "CONNECTED"
    UNRESPONSIVE = "UNRESPONSIVE"
    CRASHED = "CRASHED"
    RESTARTING = "RESTARTING"
    CLOSED = "CLOSED"


class SourceState(StrEnum):
    CLOSED = "CLOSED"
    READY = "READY"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ENDED = "ENDED"
    RECONNECTING = "RECONNECTING"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class EngineActivation:
    graph_revision: int
    report: ValidationReport
    activated: bool


@dataclass(frozen=True, slots=True)
class SourceStatus:
    node_id: UUID
    state: SourceState
    file_path: str = ""
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    source_frame_index: int | None = None
    processed_index: int = 0
    skipped_by_selection: int = 0
    dropped_before_processing: int = 0
    warnings: int = 0
    last_error: str | None = None
    source_kind: str = "video"
    display_name: str = ""
    device_id: str | None = None
    backend: str | None = None
    negotiated_fps: float | None = None
    reconnect_attempts: int = 0
    mailbox_occupancy: int = 0
    frame_age_ms: float = 0.0
    mailbox_capacity: int = 0
    processing_latency_ms: float = 0.0
    requested_width: int | None = None
    requested_height: int | None = None
    requested_fps: float | None = None


@dataclass(frozen=True, slots=True)
class EngineMetrics:
    state: EngineState = EngineState.STOPPED
    graph_revision: int | None = None
    processed_ticks: int = 0
    processed_fps: float = 0.0
    p95_node_time_ms: float = 0.0
    dropped_before_processing: int = 0
    skipped_by_selection: int = 0
    memory_bytes: int = 0
    cpu_percent: float = 0.0
    system_memory_bytes: int = 0
    uptime_s: float = 0.0
    restart_count: int = 0
    child_process_id: int | None = None
    heartbeat_age_s: float = 0.0
    mailbox_occupancy: int = 0
    mailbox_capacity: int = 0
    preview_fps: float = 0.0
    frame_age_ms: float = 0.0
    processing_latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class EngineStatus:
    connection_state: EngineConnectionState
    child_process_id: int | None = None
    graph_revision: int | None = None
    last_heartbeat_monotonic_ns: int | None = None
    restart_count: int = 0
    exit_code: int | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ImagePreview:
    node_id: UUID
    sequence: int
    tick_index: int
    width: int
    height: int
    channels: int
    data: NDArray[np.uint8]

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tick_index < 1:
            raise ValueError("preview sequence and tick index must be positive")
        if self.width < 1 or self.height < 1 or self.channels not in (3, 4):
            raise ValueError("invalid image preview dimensions")
        if self.data.dtype != np.uint8:
            raise TypeError("image previews must use uint8")
        if self.data.shape != (self.height, self.width, self.channels):
            raise ValueError("image preview data does not match its dimensions")
        if self.data.flags.writeable or not self.data.flags.c_contiguous:
            raise ValueError("image previews must be read-only and C-contiguous")


@dataclass(frozen=True, slots=True, order=True)
class NoteActivity:
    channel: int
    note: int
    velocity: int

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            raise ValueError("note-summary channel must be in the range 0..15")
        if not 0 <= self.note <= 127:
            raise ValueError("note-summary note must be in the range 0..127")
        if not 1 <= self.velocity <= 127:
            raise ValueError("note-summary velocity must be in the range 1..127")


@dataclass(frozen=True, slots=True)
class NotePreview:
    node_id: UUID
    sequence: int
    tick_index: int
    notes: tuple[NoteActivity, ...]

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tick_index < 1:
            raise ValueError("preview sequence and tick index must be positive")
        if tuple(sorted(self.notes)) != self.notes:
            raise ValueError("note summaries must use deterministic key order")


class EngineClient(Protocol):
    """Final-shaped client API used by UI and application services."""

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation: ...

    def play(self, source_node_id: UUID | None = None) -> None: ...

    def pause(self, source_node_id: UUID | None = None) -> None: ...

    def resume(self, source_node_id: UUID | None = None) -> None: ...

    def stop(self, source_node_id: UUID | None = None) -> None: ...

    def reload(self, source_node_id: UUID | None = None) -> None: ...

    def seek(self, source_node_id: UUID, source_time_s: float) -> None: ...

    def panic(self) -> None: ...

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]: ...

    def metrics(self) -> EngineMetrics: ...

    def poll_image_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[ImagePreview, ...]: ...

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]: ...

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool: ...

    def status(self) -> EngineStatus: ...

    def restart(self) -> EngineActivation | None: ...

    def close(self) -> None: ...


def freeze_uint8_preview(data: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Copy preview payload to immutable contiguous UI-client storage."""

    result = np.array(data, dtype=np.uint8, order="C", copy=True)
    result.flags.writeable = False
    return result


def freeze_metric_map(values: Mapping[str, float]) -> Mapping[str, float]:
    """Small helper reserved for process-decoded diagnostic extensions."""

    return MappingProxyType(dict(values))
