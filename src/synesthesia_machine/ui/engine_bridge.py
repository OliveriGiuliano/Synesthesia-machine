"""Deep engine-orchestration seam: the EngineBridge.

The editor drives the engine through one module. The bridge owns the
``EngineClient`` handle, the preview pump (the per-port sequence cursors live
in the injected :class:`~synesthesia_machine.ui.preview_router.PreviewRouter`),
transport dispatch, graph-activation scheduling with its debounce, and
status/telemetry normalisation. The main window becomes a thin compositor: it
forwards user intents to the bridge and renders the state the bridge publishes.

The module is testable headlessly. Everything that would normally rely on the Qt
event loop — running an engine operation off the UI thread, and the timed
activation debounce — is injected as a small protocol, so a test supplies a
synchronous task runner and a scripted clock and drives the bridge directly
without a ``QMainWindow`` or ``qWait`` timing. The preview pump itself is driven
by the compositor's timer (it needs the note dock's visibility flag the window
holds), so the bridge exposes a single ``pump_previews_once`` step rather than
owning that timer; the step asks the injected preview router to poll, and the
compositor applies the router's routing decision to the scene and panels.

The bridge publishes two kinds of output. Rich results (activation reports,
telemetry, device catalogues, profiles, restart/transport outcomes) are folded
into the published :class:`EngineBridgeState` so the compositor renders typed
data; payload-free events (task failures, the "refreshing devices" notice) go
through ``on_status_message`` as a stable key plus a status-bar timeout.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    DeviceCatalogue,
    EngineActivation,
    EngineClient,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NodeProfile,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.ui.preview_router import PreviewRouter, PumpedPreviews

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot

__all__ = [
    "EngineBridge",
    "EngineBridgeState",
    "EngineRestartOutcome",
    "EngineTaskRunner",
    "PumpedPreviews",
    "TransportOutcome",
    "UiClock",
]

_TelemetrySnapshot = tuple[
    EngineMetrics,
    tuple[SourceStatus, ...],
    tuple[MidiOutputStatus, ...],
    NodeMemoryDiagnostic | None,
]


class EngineTaskRunner(Protocol):
    """Runs one synchronous engine operation and reports the outcome.

    Production runs the operation on a worker pool and delivers the callbacks on
    the UI thread (via Qt signals); a test runner executes it in place. ``submit``
    returns ``False`` without running the operation when ``kind`` is already in
    flight, so callers never double-issue the same logical operation.
    """

    def submit(
        self,
        kind: str,
        operation: Callable[[], object],
        *,
        on_result: Callable[[str, object], None],
        on_error: Callable[[str, object], None],
    ) -> bool: ...


class UiClock(Protocol):
    """Schedules a one-shot callback for the timed activation debounce.

    Production is a single-shot ``QTimer``; a test clock records the scheduled
    callback so a test can fire the debounce deterministically.
    """

    def schedule_once(self, delay_ms: int, callback: Callable[[], None]) -> None: ...

    def cancel(self, callback: Callable[[], None]) -> None: ...


@dataclass(frozen=True, slots=True)
class TransportOutcome:
    """A completed transport command: the status verb and the source it hit."""

    verb: str
    target: UUID


@dataclass(frozen=True, slots=True)
class EngineRestartOutcome:
    """A completed engine restart: ``ok``, ``rejected``, ``restart_failed``,
    or ``rebuild_failed``, plus a failure detail for the two error outcomes."""

    outcome: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EngineBridgeState:
    """Normalized engine/transport state the compositor renders from.

    The ``last_*`` fields carry the most recent settled result of each task
    family; a publish reuses the previous instance until a new result lands, so
    the compositor tells new events from stale state by object identity.
    """

    connection_state: EngineConnectionState
    engine_state: EngineState
    source_statuses: tuple[SourceStatus, ...] = ()
    midi_output_statuses: tuple[MidiOutputStatus, ...] = ()
    device_catalogue: DeviceCatalogue | None = None
    metrics: EngineMetrics | None = None
    node_profiles: tuple[NodeProfile, ...] = ()
    status: EngineStatus | None = None
    diagnostic: NodeMemoryDiagnostic | None = None
    last_activation: EngineActivation | None = None
    last_transport: TransportOutcome | None = None
    last_restart: EngineRestartOutcome | None = None


class EngineBridge:
    """Owns the engine client and every behaviour the editor uses to drive it."""

    ACTIVATION_DEBOUNCE_MS = 100

    def __init__(
        self,
        client: EngineClient,
        *,
        demand_roots_for: Callable[[GraphSnapshot], tuple[UUID, ...]],
        task_runner: EngineTaskRunner,
        clock: UiClock,
        preview_router: PreviewRouter,
        on_previews: Callable[[PumpedPreviews], None],
        on_state: Callable[[EngineBridgeState], None],
        on_status_message: Callable[[str, int], None],
    ) -> None:
        self._client = client
        self._demand_roots_for = demand_roots_for
        self._task_runner = task_runner
        self._clock = clock
        self._preview_router = preview_router
        self._on_previews = on_previews
        self._on_state = on_state
        self._on_status_message = on_status_message

        # Activation scheduling.
        self._pending_activation: tuple[GraphSnapshot, tuple[UUID, ...]] | None = None
        self._tasks_inflight: set[str] = set()
        self._closed = False
        # One stable callback object so the clock's identity-based cancel
        # (and the test clock's) can match the scheduled debounce.
        self._flush: Callable[[], None] = self._flush_activation

        # Renderable state.
        self._state = EngineBridgeState(
            connection_state=EngineConnectionState.CLOSED,
            engine_state=EngineState.STOPPED,
        )

    # -- lifecycle ---------------------------------------------------------

    @property
    def state(self) -> EngineBridgeState:
        return self._state

    def close(self) -> None:
        """Cancel the pending activation and close the underlying client."""
        if self._closed:
            return
        self._closed = True
        self._pending_activation = None
        self._clock.cancel(self._flush)
        self._client.close()

    # -- activation (debounced) -------------------------------------------

    def schedule_activation(self, snapshot: GraphSnapshot) -> None:
        """Record the latest graph and fire a single debounced activation.

        Repeated calls before the debounce elapses collapse into one activation
        of the most recent snapshot, matching the historical 100 ms behaviour.
        """
        if self._closed:
            return
        self._pending_activation = (snapshot, self._demand_roots_for(snapshot))
        self._clock.schedule_once(self.ACTIVATION_DEBOUNCE_MS, self._flush)

    def activate_now(self, snapshot: GraphSnapshot) -> None:
        """Record the given graph and flush its activation without the debounce."""
        if self._closed:
            return
        self._pending_activation = (snapshot, self._demand_roots_for(snapshot))
        self._flush()

    def _flush_activation(self) -> None:
        pending = self._pending_activation
        self._pending_activation = None
        if pending is None or self._closed or "activation" in self._tasks_inflight:
            return
        snapshot, demand_roots = pending
        self._submit(
            "activation",
            lambda: self._client.activate(snapshot, demand_roots=demand_roots),
        )

    def _apply_activation(self, activation: EngineActivation) -> None:
        self._state = replace(self._state, last_activation=activation)
        if activation.activated:
            self.clear_runtime_previews()
            self._on_status_message("activated", 3000)
        else:
            self.clear_runtime_previews()
            # The engine kept running previews mapped/retained while it was
            # active; forget them client-side so the next poll cannot re-show
            # the last frame as if the engine were still producing.
            self._client.clear_previews()
            self._on_status_message("activation_rejected", 5000)
        self._publish_state()

    # -- transport ---------------------------------------------------------

    def play(self, source_node_id: UUID | None = None) -> None:
        self._submit("transport", lambda: self._client.play(source_node_id))

    def play_or_resume(self, source_node_id: UUID | None = None) -> None:
        """Play the targeted source, resuming it first if it is paused.

        The source-state lookup and the play/resume command are both engine
        IPC, so they run together on the task pool (a slow or hung child
        cannot block the UI event thread). The outcome carries the verb the
        compositor reports ("Playing" or "Resumed").
        """
        if source_node_id is None:
            self.play(source_node_id)
            return
        self._submit("transport", lambda: self._load_play(source_node_id))

    def _load_play(self, target: UUID) -> TransportOutcome:
        statuses = self._client.source_status(target)
        if statuses and statuses[0].state is SourceState.PAUSED:
            self._client.resume(target)
            return TransportOutcome("Resumed", target)
        self._client.play(target)
        return TransportOutcome("Playing", target)

    def pause(self, source_node_id: UUID | None = None) -> None:
        if source_node_id is None:
            self._submit("transport", lambda: self._client.pause(source_node_id))
            return
        self._submit(
            "transport",
            lambda: self._transport_command("pause", "Paused", source_node_id),
        )

    def stop_source(self, source_node_id: UUID | None = None) -> None:
        if source_node_id is None:
            self._submit("transport", lambda: self._client.stop(source_node_id))
            return
        self._submit(
            "transport",
            lambda: self._transport_command("stop", "Stopped", source_node_id),
        )

    def reload(self, source_node_id: UUID | None = None) -> None:
        if source_node_id is None:
            self._submit("transport", lambda: self._client.reload(source_node_id))
            return
        self._submit(
            "transport",
            lambda: self._transport_command("reload", "Reloaded", source_node_id),
        )

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        self._submit("transport", lambda: self._client.seek(source_node_id, source_time_s))

    def _transport_command(self, method: str, verb: str, target: UUID) -> TransportOutcome:
        getattr(self._client, method)(target)
        return TransportOutcome(verb, target)

    def panic(self) -> None:
        self._submit("panic", lambda: self._client.panic())

    def restart(self, snapshot: GraphSnapshot) -> None:
        """Restart the engine and rebuild the given graph if it was not active."""
        self._submit("restart", lambda: self._load_restart(snapshot))

    def _load_restart(self, snapshot: GraphSnapshot) -> EngineRestartOutcome:
        try:
            activation = self._client.restart()
        except (RuntimeError, TimeoutError) as error:
            return EngineRestartOutcome("restart_failed", str(error))
        if activation is None and snapshot.nodes:
            try:
                activation = self._client.activate(snapshot)
            except (RuntimeError, TimeoutError) as error:
                return EngineRestartOutcome("rebuild_failed", str(error))
        if activation is not None and not activation.activated:
            return EngineRestartOutcome("rejected")
        return EngineRestartOutcome("ok")

    # -- preview pump ------------------------------------------------------

    def pump_previews_once(self, *, note_visible: bool) -> None:
        """Poll the preview ports and hand the batch to the compositor callback.

        Driven by the compositor's timer (which supplies the note dock's
        visibility flag); the injected preview router owns the per-port
        sequence cursors.
        """
        if self._closed:
            return
        pumped = self._preview_router.poll(self._client, note_visible=note_visible)
        if pumped is None:
            return
        self._on_previews(pumped)

    def note_preview_hidden(self) -> None:
        """Drop the note cursors when the note dock is hidden."""
        self._preview_router.clear_note_cursors()

    def image_preview_hidden(self) -> None:
        """Drop the image cursors when the image dock is hidden."""
        self._preview_router.clear_image_cursors()

    def clear_runtime_previews(self) -> None:
        self._preview_router.clear_cursors()

    # -- status / telemetry ------------------------------------------------

    def refresh_status(self) -> None:
        """Poll the connection state + publish it (driven by the compositor timer)."""
        if self._closed:
            return
        try:
            status = self._client.status()
        except (RuntimeError, TimeoutError):
            return
        self._state = replace(
            self._state,
            status=status,
            connection_state=status.connection_state,
        )
        self._publish_state()

    def refresh_telemetry(self, selected_node_id: UUID | None = None) -> None:
        """Collect metrics, source/MIDI statuses, and (optionally) one node's
        memory diagnostic on the task pool, then publish them as state."""
        self._submit("refresh", lambda: self._load_telemetry(selected_node_id))

    def _load_telemetry(self, selected_node_id: UUID | None) -> _TelemetrySnapshot:
        metrics = self._client.metrics()
        sources = self._client.source_status()
        midi_outputs = self._client.midi_output_status()
        diagnostic = None
        if selected_node_id is not None:
            diagnostics = self._client.node_memory_diagnostics(selected_node_id)
            diagnostic = diagnostics[0] if diagnostics else None
        return metrics, sources, midi_outputs, diagnostic

    def _apply_telemetry(self, result: _TelemetrySnapshot) -> None:
        metrics, sources, midi_outputs, diagnostic = result
        self._state = replace(
            self._state,
            metrics=metrics,
            source_statuses=sources,
            midi_output_statuses=midi_outputs,
            diagnostic=diagnostic,
        )
        self._publish_state()

    def read_diagnostics(self) -> tuple[EngineMetrics | None, tuple[NodeProfile, ...]]:
        """One-shot metrics/profile read on the calling thread.

        Returns ``(None, ())`` when the engine is closed or unreachable, so a
        diagnostic bundle can still be exported after an engine failure.
        """
        if self._closed:
            return None, ()
        try:
            return self._client.metrics(), self._client.node_profiles()
        except (RuntimeError, TimeoutError):
            return None, ()

    # -- runtime profiling (lightweight UI-thread IPC) ---------------------

    def set_profiling_enabled(self, enabled: bool) -> None:
        self._client.set_profiling_enabled(enabled)

    def reset_profiling(self) -> None:
        self._client.reset_profiling()

    def refresh_profiles(self) -> None:
        """Poll the per-node profiles on the calling thread and publish them."""
        if self._closed:
            return
        try:
            profiles = self._client.node_profiles()
        except (RuntimeError, TimeoutError):
            return
        self.publish_node_profiles(tuple(profiles))

    # -- devices -----------------------------------------------------------

    def publish_metrics(self, metrics: EngineMetrics) -> None:
        self._state = replace(self._state, engine_state=metrics.state, metrics=metrics)
        self._publish_state()

    def publish_device_catalogue(self, catalogue: DeviceCatalogue) -> None:
        self._state = replace(self._state, device_catalogue=catalogue)
        self._publish_state()

    def publish_node_profiles(self, profiles: tuple[NodeProfile, ...]) -> None:
        self._state = replace(self._state, node_profiles=profiles)
        self._publish_state()

    def request_device_catalogue(self, *, force_refresh: bool = False) -> None:
        submitted = self._submit(
            "devices", lambda: self._client.device_catalogue(force_refresh=force_refresh)
        )
        if submitted and force_refresh:
            self._on_status_message("devices_refreshing", 3000)

    # -- task plumbing -----------------------------------------------------

    def _submit(self, kind: str, operation: Callable[[], object]) -> bool:
        if self._closed or kind in self._tasks_inflight:
            return False
        self._tasks_inflight.add(kind)
        return self._task_runner.submit(
            kind,
            operation,
            on_result=self._on_task_result,
            on_error=self._on_task_error,
        )

    def _on_task_result(self, kind: str, result: object) -> None:
        self._tasks_inflight.discard(kind)
        if self._closed:
            return
        if kind == "activation" and isinstance(result, EngineActivation):
            self._apply_activation(result)
        elif kind == "devices" and isinstance(result, DeviceCatalogue):
            self.publish_device_catalogue(result)
        elif kind == "transport" and isinstance(result, TransportOutcome):
            self._state = replace(self._state, last_transport=result)
            self._publish_state()
        elif kind == "restart" and isinstance(result, EngineRestartOutcome):
            self._state = replace(self._state, last_restart=result)
            self._publish_state()
        elif kind == "refresh":
            self._apply_telemetry(cast("_TelemetrySnapshot", result))
        elif kind == "panic":
            self._on_status_message("panic_ok", 3000)
        else:
            self._publish_state()

    def _on_task_error(self, kind: str, error: object) -> None:
        del error
        self._tasks_inflight.discard(kind)
        if self._closed:
            return
        if kind == "activation":
            self._on_status_message("activation_failed", 5000)
            self._flush_activation()
        elif kind == "devices":
            self._on_status_message("devices_failed", 8000)
        elif kind == "transport":
            self._on_status_message("transport_failed", 5000)
        elif kind == "panic":
            self._on_status_message("panic_failed", 5000)
        elif kind == "restart":
            self._on_status_message("restart_failed", 8000)

    # -- helpers -----------------------------------------------------------

    def _publish_state(self) -> None:
        self._on_state(self._state)
