"""In-process engine client: the in-process placement's protocol hat (ADR-0026).

The in-process placement composes the placement-independent engine body
with the transport-independent ``EngineClient`` protocol. This client holds
no engine state of its own: every method either delegates to the body or
is client-only behaviour (a connected ``status``, a user restart from the
body's last valid plan, and preview clearing). The spawned child placement
never builds this class; it runs the body directly behind the wire.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from uuid import UUID

from synesthesia_machine.contracts import (
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
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.runtime.device_catalogue import DeviceCatalogueService
from synesthesia_machine.runtime.engine_body import (
    CameraSourceFactory,
    EngineBody,
    LatestFrameGraphWorker,
    SourceController,
    SourceMailboxMetrics,
    TickObserver,
    VideoSourceFactory,
)
from synesthesia_machine.runtime.preview_channel import PreviewTransport

__all__ = [
    "CameraSourceFactory",
    "InProcessEngineClient",
    "LatestFrameGraphWorker",
    "SourceController",
    "SourceMailboxMetrics",
    "TickObserver",
    "VideoSourceFactory",
]


class InProcessEngineClient:
    """In-process placement of the EngineClient protocol: a thin hat over the EngineBody."""

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        video_source_factory: VideoSourceFactory | None = None,
        camera_source_factory: CameraSourceFactory | None = None,
        device_catalogue_service: DeviceCatalogueService | None = None,
        worker_clock: Callable[[], int] = time.perf_counter_ns,
        tick_observer: TickObserver | None = None,
        use_previews: bool = True,
        preview_dirty_notifier: Callable[[], None] | None = None,
        preview_transport: PreviewTransport | None = None,
    ) -> None:
        self._body = EngineBody(
            registry,
            video_source_factory=video_source_factory,
            camera_source_factory=camera_source_factory,
            device_catalogue_service=device_catalogue_service,
            worker_clock=worker_clock,
            tick_observer=tick_observer,
            use_previews=use_previews,
            preview_dirty_notifier=preview_dirty_notifier,
            preview_transport=preview_transport,
        )

    # -- Protocol surface: one delegation per body capability -------------

    def activate(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
        reset_reason: ResetReason = ResetReason.PLAN_REPLACED,
    ) -> EngineActivation:
        return self._body.activate(snapshot, demand_roots=demand_roots, reset_reason=reset_reason)

    def play(self, source_node_id: UUID | None = None) -> None:
        self._body.play(source_node_id)

    def pause(self, source_node_id: UUID | None = None) -> None:
        self._body.pause(source_node_id)

    def resume(self, source_node_id: UUID | None = None) -> None:
        self._body.resume(source_node_id)

    def stop(self, source_node_id: UUID | None = None) -> None:
        self._body.stop(source_node_id)

    def reload(self, source_node_id: UUID | None = None) -> None:
        self._body.reload(source_node_id)

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        self._body.seek(source_node_id, source_time_s)

    def panic(self) -> None:
        self._body.panic()

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        return self._body.source_status(source_node_id)

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        return self._body.midi_output_status(output_node_id)

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        return self._body.device_catalogue(force_refresh=force_refresh)

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        return self._body.node_memory_diagnostics(node_id)

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        return self._body.node_profiles()

    def set_profiling_enabled(self, enabled: bool) -> None:
        self._body.set_profiling_enabled(enabled)

    def reset_profiling(self) -> None:
        self._body.reset_profiling()

    def metrics(self) -> EngineMetrics:
        return self._body.metrics()

    def next_image_previews(self) -> tuple[ImagePreview, ...]:
        return self._body.next_image_previews()

    def next_note_previews(self) -> tuple[NotePreview, ...]:
        return self._body.next_note_previews()

    def next_value_previews(self) -> tuple[ValuePreview, ...]:
        return self._body.next_value_previews()

    def reset_image_preview_cursors(self) -> None:
        self._body.reset_image_preview_cursors()

    def reset_note_preview_cursors(self) -> None:
        self._body.reset_note_preview_cursors()

    def clear_previews(self) -> None:
        self._body.clear_previews()

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        return self._body.wait_until_idle(timeout_s)

    def close(self) -> None:
        self._body.close()

    # -- Client-only behaviour: composition over the body ------------------

    def status(self) -> EngineStatus:
        # The in-process placement never loses its connection: the only
        # state transitions are CLOSED after close() and everything else
        # is CONNECTED.
        connection_state = (
            EngineConnectionState.CLOSED
            if self._body.state is EngineState.CLOSED
            else EngineConnectionState.CONNECTED
        )
        return EngineStatus(connection_state, graph_revision=self._body.graph_revision)

    def restart(self) -> EngineActivation | None:
        source = self._body.last_activation()
        if source is None:
            return None
        snapshot, demand_roots = source
        return self._body.activate(
            snapshot,
            demand_roots=demand_roots,
            reset_reason=ResetReason.ENGINE_RESTARTED,
        )
