"""Client-facing engine control, status, metrics, and preview contracts.

These values intentionally describe the stable UI-side API rather than the
in-process implementation.  A later process-backed client can implement the
same protocol without changing transport or widget code.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts.validation import ValidationReport

if TYPE_CHECKING:
    # Type-only reference: the client protocol names the graph snapshot it
    # accepts; no runtime edge from contracts to graph is created.
    from synesthesia_machine.graph.model import GraphSnapshot

#: Default heartbeat liveness deadline in seconds.  Owned by the process
#: client: it uses this deadline to fold a stale child heartbeat into the
#: connection state it reports, so the deadline lives in exactly one place.
DEFAULT_HEARTBEAT_TIMEOUT_S = 2.0


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


class MidiOutputConnectionState(StrEnum):
    UNSELECTED = "UNSELECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


class DeviceKind(StrEnum):
    CAMERA_INPUT = "CAMERA_INPUT"
    MIDI_OUTPUT = "MIDI_OUTPUT"
    AUDIO_OUTPUT = "AUDIO_OUTPUT"


@dataclass(frozen=True, slots=True, order=True)
class DeviceDescriptor:
    """Stable engine-owned device identity paired with a user-facing label."""

    kind: DeviceKind
    device_id: str
    display_name: str
    is_default: bool = False

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("device display name must not be empty")
        if not self.device_id and not self.is_default:
            raise ValueError("only a default device may use an empty device ID")


@dataclass(frozen=True, slots=True)
class DeviceCatalogue:
    """Deterministic partial hardware catalogue safe to carry across engine IPC."""

    devices: tuple[DeviceDescriptor, ...] = ()
    errors: tuple[tuple[DeviceKind, str], ...] = ()
    pending_kinds: tuple[DeviceKind, ...] = ()

    def __post_init__(self) -> None:
        if tuple(sorted(self.devices)) != self.devices:
            raise ValueError("devices must use deterministic kind, ID, and label order")
        identities = tuple((device.kind, device.device_id) for device in self.devices)
        if len(set(identities)) != len(identities):
            raise ValueError("device IDs must be unique within each device kind")
        if tuple(sorted(self.errors)) != self.errors:
            raise ValueError("device catalogue errors must use deterministic kind order")
        if tuple(sorted(set(self.pending_kinds))) != self.pending_kinds:
            raise ValueError("pending device kinds must be unique and deterministic")

    def for_kind(self, kind: DeviceKind) -> tuple[DeviceDescriptor, ...]:
        return tuple(device for device in self.devices if device.kind is kind)


class ResetReason(StrEnum):
    PLAN_REPLACED = "PLAN_REPLACED"
    SOURCE_RESTARTED = "SOURCE_RESTARTED"
    SOURCE_ENDED = "SOURCE_ENDED"
    SEEK = "SEEK"
    PARAMETER_CHANGED = "PARAMETER_CHANGED"
    CLOCK_CHANGED = "CLOCK_CHANGED"
    ENGINE_RESTARTED = "ENGINE_RESTARTED"


@dataclass(frozen=True, slots=True)
class NodeExecutionError:
    """Structured node failure safe to carry across the engine boundary."""

    node_id: UUID
    code: str
    message: str
    details: str | None
    recoverable: bool
    tick_index: int | None


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
    source_time_s: float | None = None
    source_frame_index: int | None = None
    processed_index: int = 0
    #: Processed index the source will have reached at the end of its active
    #: region (ADR-0025); ``None`` when the container reports neither a frame
    #: count nor a frame rate.  Camera sources publish ``None``.
    total_index: int | None = None
    #: End of the played segment in source time, clamped to the container
    #: duration (ADR-0025); ``None`` when unknown.  Camera sources publish
    #: ``None``.
    region_end_s: float | None = None
    #: Start of the played segment in source time (ADR-0028); ``None`` when
    #: the container reports no duration.  Camera sources publish ``None``.
    region_start_s: float | None = None
    #: Non-fatal region fact (ADR-0028): a message describing a loop
    #: configuration the file cannot honour, whose resolved region is the
    #: fallback; ``None`` when the configuration is honoured as-is.  Camera
    #: sources and failed sources publish ``None``.
    region_error: str | None = None
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
class MidiOutputStatus:
    node_id: UUID
    connection_state: MidiOutputConnectionState
    selected_port: str = ""
    available_ports: tuple[str, ...] = ()
    active_note_count: int = 0
    active_channels: tuple[int, ...] = ()
    dropped_state_updates: int = 0
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.active_note_count < 0 or self.dropped_state_updates < 0:
            raise ValueError("MIDI output counters cannot be negative")
        if tuple(sorted(set(self.available_ports))) != tuple(sorted(self.available_ports)):
            raise ValueError("MIDI output names must be unique")
        if tuple(sorted(set(self.active_channels))) != self.active_channels:
            raise ValueError("active MIDI channels must be sorted and unique")
        if any(not 0 <= channel <= 15 for channel in self.active_channels):
            raise ValueError("active MIDI channels must be in the range 0..15")


@dataclass(frozen=True, slots=True)
class NodeMemoryDiagnostic:
    """Compact node-owned memory estimate safe to publish across the engine boundary."""

    node_id: UUID
    estimated_retained_bytes: int = 0
    retained_bytes: int = 0
    retained_frame_count: int = 0
    capacity_frame_count: int = 0
    memory_limit_bytes: int = 0

    def __post_init__(self) -> None:
        values = (
            self.estimated_retained_bytes,
            self.retained_bytes,
            self.retained_frame_count,
            self.capacity_frame_count,
            self.memory_limit_bytes,
        )
        if any(value < 0 for value in values):
            raise ValueError("node memory diagnostics cannot contain negative values")
        if self.retained_frame_count > self.capacity_frame_count:
            raise ValueError("retained frame count cannot exceed configured capacity")


@dataclass(frozen=True, slots=True)
class NodeProfile:
    """Compact rolling node profile safe to transport across the process boundary."""

    node_id: UUID
    invocation_count: int = 0
    error_count: int = 0
    window_size: int = 0
    last_duration_ms: float = 0.0
    ema_duration_ms: float = 0.0
    p50_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    output_summary: str = "no outputs"
    output_bytes: int = 0

    def __post_init__(self) -> None:
        counters = (self.invocation_count, self.error_count, self.window_size, self.output_bytes)
        durations = (
            self.last_duration_ms,
            self.ema_duration_ms,
            self.p50_duration_ms,
            self.p95_duration_ms,
            self.max_duration_ms,
        )
        if any(value < 0 for value in counters):
            raise ValueError("node profile counters cannot be negative")
        if self.error_count > self.invocation_count or self.window_size > self.invocation_count:
            raise ValueError("node profile counters are inconsistent")
        if any(not math.isfinite(value) or value < 0.0 for value in durations):
            raise ValueError("node profile durations must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class EngineSupervisorFacts:
    """Process-supervision facts, kept out of the engine telemetry record.

    The fields are filled by different owners per engine placement, and a
    zero/None value means "no such process" rather than a measurement:

    - In-process placement: the record is always the vacuous default
      (``EngineSupervisorFacts()``) — no child process exists, so every
      field stays at its zero/None default.
    - Process placement: the spawned child fills ``child_process_id``,
      ``uptime_s``, ``cpu_percent`` and ``system_memory_bytes`` (facts
      about itself, on the metrics wire reply), and the parent client fills
      ``restart_count`` and ``heartbeat_age_s`` (facts about its own
      supervision of the child). ``child_process_id`` is ``None`` only
      before the child has ever been spawned.
    """

    child_process_id: int | None = None
    uptime_s: float = 0.0
    cpu_percent: float = 0.0
    system_memory_bytes: int = 0
    restart_count: int = 0
    heartbeat_age_s: float = 0.0


@dataclass(frozen=True, slots=True)
class EngineMetrics:
    """Engine telemetry for one query; supervision lives in ``supervisor``.

    The engine body fills the telemetry fields in both placements; the
    placement layers fill ``supervisor`` (see :class:`EngineSupervisorFacts`
    for the per-field owners and the per-placement vacuity), so a consumer
    can never mistake a vacuous in-process default for a measurement.
    """

    state: EngineState = EngineState.STOPPED
    graph_revision: int | None = None
    processed_ticks: int = 0
    input_fps: float = 0.0
    processed_fps: float = 0.0
    p95_node_time_ms: float = 0.0
    dropped_before_processing: int = 0
    skipped_by_selection: int = 0
    memory_bytes: int = 0
    mailbox_occupancy: int = 0
    mailbox_capacity: int = 0
    preview_fps: float = 0.0
    frame_age_ms: float = 0.0
    processing_latency_ms: float = 0.0
    graph_latency_window_size: int = 0
    p50_graph_execution_ms: float = 0.0
    p95_graph_execution_ms: float = 0.0
    p99_graph_execution_ms: float = 0.0
    max_graph_execution_ms: float = 0.0
    runtime_errors: tuple[NodeExecutionError, ...] = ()
    supervisor: EngineSupervisorFacts = field(default_factory=EngineSupervisorFacts)


@dataclass(frozen=True, slots=True)
class EngineStatus:
    connection_state: EngineConnectionState
    child_process_id: int | None = None
    graph_revision: int | None = None
    last_heartbeat_monotonic_ns: int | None = None
    restart_count: int = 0
    exit_code: int | None = None
    last_error: str | None = None
    crash_log_path: str | None = None


@dataclass(frozen=True, slots=True)
class ImagePreview:
    owner_id: UUID
    source_port_id: str
    sequence: int
    tick_index: int
    width: int
    height: int
    channels: int
    data: NDArray[np.uint8]

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tick_index < 1:
            raise ValueError("preview sequence and tick index must be positive")
        if not self.source_port_id:
            raise ValueError("image preview source port must be non-empty")
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
    owner_id: UUID
    sequence: int
    tick_index: int
    notes: tuple[NoteActivity, ...]

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tick_index < 1:
            raise ValueError("preview sequence and tick index must be positive")
        if tuple(sorted(self.notes)) != self.notes:
            raise ValueError("note summaries must use deterministic key order")


@dataclass(frozen=True, slots=True)
class ValuePreview:
    """Compact scalar (INT/FLOAT) preview carried in-band for connection previews."""

    owner_id: UUID
    source_port_id: str
    sequence: int
    tick_index: int
    port_type: str
    text: str

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tick_index < 1:
            raise ValueError("preview sequence and tick index must be positive")
        if not self.source_port_id:
            raise ValueError("value preview source port must be non-empty")
        if not self.port_type:
            raise ValueError("value preview port type must be non-empty")
        if not self.text:
            raise ValueError("value preview text must be non-empty")


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

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]: ...

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue: ...

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]: ...

    def node_profiles(self) -> tuple[NodeProfile, ...]: ...

    def set_profiling_enabled(self, enabled: bool) -> None: ...

    def reset_profiling(self) -> None: ...

    def metrics(self) -> EngineMetrics: ...

    def next_image_previews(self) -> tuple[ImagePreview, ...]: ...

    def next_note_previews(self) -> tuple[NotePreview, ...]: ...

    def next_value_previews(self) -> tuple[ValuePreview, ...]: ...

    def reset_image_preview_cursors(self) -> None: ...

    def reset_note_preview_cursors(self) -> None: ...

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool: ...

    def status(self) -> EngineStatus: ...

    def restart(self) -> EngineActivation | None: ...

    def clear_previews(self) -> None: ...

    def close(self) -> None: ...


def freeze_uint8_preview(data: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Copy preview payload to immutable contiguous UI-client storage."""

    result = np.array(data, dtype=np.uint8, order="C", copy=True)
    result.flags.writeable = False
    return result
