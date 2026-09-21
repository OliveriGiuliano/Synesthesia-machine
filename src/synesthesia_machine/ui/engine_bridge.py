"""Deep engine-orchestration seam: the EngineBridge.

The editor drives the engine through one module. The bridge owns the
:class:`~synesthesia_machine.runtime.engine_session.EngineSession` — the
Qt-free module that drives the engine client: per-port preview sequence
cursors, the connection state the client folds from liveness, the "engine
is known stopped" cache, and the restart-outcome mapping (the ADR-0013/0020
policies). The full client protocol surface is one hop away through the
session's ``client`` property, which the bridge reaches for the reads it
publishes. On top of it the bridge owns the Qt-facing concerns: transport
and telemetry dispatch over the injected task runner, graph-activation
scheduling with its debounce, and status normalisation. The main window
becomes a thin compositor: it forwards user intents to the bridge and
renders the state the bridge publishes.

The module is testable headlessly. Everything that would normally rely on
the Qt event loop — running an engine operation off the UI thread, and the
timed activation debounce — is injected as a small protocol, so a test
supplies a synchronous task runner and a scripted clock and drives the
bridge directly without a ``QMainWindow`` or ``qWait`` timing. The preview
pump itself is driven by the compositor's timer (it needs the note dock's
visibility flag the window holds), so the bridge exposes a single
``pump_previews_once`` step rather than owning that timer; the step asks
the session for the previews newer than its cursors, routes the batch with
the bridge's preview router, and hands the compositor the sink decisions
to apply to the scene and panels.

The bridge publishes two kinds of output. Rich results (activation reports,
telemetry, device catalogues, profiles, restart/transport outcomes) are
folded into the published :class:`EngineBridgeState` so the compositor
renders typed data; payload-free events (task failures, the "refreshing
devices" notice) go through ``on_status_message`` as a stable key plus a
status-bar timeout.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    DeviceCatalogue,
    EngineActivation,
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
from synesthesia_machine.runtime import EngineSession
from synesthesia_machine.ui.preview_router import PreviewRouter, PumpedPreviews, RoutingResult

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot
    from synesthesia_machine.ui.view_models import GraphViewModel

__all__ = [
    "EngineBridge",
    "EngineBridgeState",
    "EngineRestartOutcome",
    "EngineTaskRunner",
    "RoutingResult",
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
    ``known_stopped`` mirrors the session's "engine is stopped, skip the
    metrics round trip" cache so the compositor's periodic tick can reuse the
    optimization without keeping a private copy.
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
    known_stopped: bool = False


class EngineBridge:
    """Owns the engine session and every behaviour the editor uses to drive it."""

    ACTIVATION_DEBOUNCE_MS = 100

    def __init__(
        self,
        session: EngineSession,
        *,
        demand_roots_for: Callable[[GraphSnapshot], tuple[UUID, ...]],
        task_runner: EngineTaskRunner,
        clock: UiClock,
        on_previews: Callable[[RoutingResult], None],
        on_state: Callable[[EngineBridgeState], None],
        on_status_message: Callable[[str, int], None],
    ) -> None:
        self._session = session
        self._demand_roots_for = demand_roots_for
        self._task_runner = task_runner
        self._clock = clock
        # The pump step owns the routing policy: a stateless router the
        # bridge applies to every pumped batch before the compositor sees
        # it, so the interface only advertises behaviour it performs.
        self._preview_router = PreviewRouter()
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
        """Cancel the pending activation and close the session (and its client)."""
        if self._closed:
            return
        self._closed = True
        self._pending_activation = None
        self._clock.cancel(self._flush)
        self._session.close()

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
        if pending is None or self._closed:
            return
        if "activation" in self._tasks_inflight:
            # The in-flight activation still owns the engine: keep the newer
            # snapshot queued so it is flushed when that one finishes
            # (_on_task_result re-flushes). Dropping it would leave the
            # engine running the stale plan - e.g. loop knobs that appear
            # to do nothing while the video keeps playing from before.
            self._pending_activation = pending
            return
        snapshot, demand_roots = pending
        self._submit(
            "activation",
            lambda: self._session.activate(snapshot, demand_roots=demand_roots),
        )

    def _apply_activation(self, activation: EngineActivation) -> None:
        # The session cleared the client's preview state as part of the
        # activation, so the bridge only reports and publishes.
        self._state = replace(self._state, last_activation=activation)
        if activation.activated:
            self._on_status_message("activated", 3000)
        else:
            self._on_status_message("activation_rejected", 5000)
        self._publish_state()

    def play(self, source_node_id: UUID | None = None) -> None:
        self._submit("transport", lambda: self._session.play(source_node_id))

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
        statuses = self._session.client.source_status(target)
        if statuses and statuses[0].state is SourceState.PAUSED:
            self._session.resume(target)
            return TransportOutcome("Resumed", target)
        self._session.play(target)
        return TransportOutcome("Playing", target)

    def toggle_play_pause(self, source_node_id: UUID) -> None:
        """Toggle the targeted source between play and pause (Space gesture).

        The play-or-pause decision depends on the source's state, so the state
        lookup and the command run together on the task pool (a slow or hung
        child cannot block the UI event thread). A playing target pauses; any
        other state plays (a paused target resumes).
        """
        self._submit("transport", lambda: self._load_toggle(source_node_id))

    def _load_toggle(self, target: UUID) -> TransportOutcome:
        statuses = self._session.client.source_status(target)
        if statuses and statuses[0].state is SourceState.PLAYING:
            return self._transport_command("pause", "Paused", target)
        return self._load_play(target)

    def pause(self, source_node_id: UUID | None = None) -> None:
        if source_node_id is None:
            self._submit("transport", lambda: self._session.pause(source_node_id))
            return
        self._submit(
            "transport",
            lambda: self._transport_command("pause", "Paused", source_node_id),
        )

    def stop_source(self, source_node_id: UUID | None = None) -> None:
        if source_node_id is None:
            self._submit("transport", lambda: self._session.stop(source_node_id))
            return
        self._submit(
            "transport",
            lambda: self._transport_command("stop", "Stopped", source_node_id),
        )

    def reload(self, source_node_id: UUID | None = None) -> None:
        if source_node_id is None:
            self._submit("transport", lambda: self._session.reload(source_node_id))
            return
        self._submit(
            "transport",
            lambda: self._transport_command("reload", "Reloaded", source_node_id),
        )

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        self._submit("transport", lambda: self._session.seek(source_node_id, source_time_s))

    def _transport_command(self, method: str, verb: str, target: UUID) -> TransportOutcome:
        getattr(self._session, method)(target)
        return TransportOutcome(verb, target)

    def panic(self) -> None:
        self._submit("panic", lambda: self._session.panic())

    def restart(self) -> None:
        """Restart the engine through the session.

        The session rebuilds the last *valid* (accepted) graph — never a
        possibly-broken current draft — which is the ADR-0013/0020
        restart policy.
        """
        self._submit("restart", self._load_restart)

    def _load_restart(self) -> EngineRestartOutcome:
        outcome = self._session.restart()
        return EngineRestartOutcome(outcome=outcome.reason, detail=outcome.detail)

    # -- preview pump ------------------------------------------------------

    def note_preview_hidden(self) -> None:
        """Drop the note cursors when the note dock is hidden."""
        self._session.reset_note_preview_cursors()

    def image_preview_hidden(self) -> None:
        """Drop the image cursors when the image dock is hidden."""
        self._session.reset_image_preview_cursors()

    # -- status / telemetry ------------------------------------------------

    def refresh_status(self) -> None:
        """Poll the folded connection state + publish it (compositor timer).

        The status read is one round trip folded into the six-state machine
        by the session; an unreachable child folds to CRASHED (status ``None``)
        instead of raising.
        """
        if self._closed:
            return
        state, status = self._session.poll_status()
        self._state = replace(self._state, connection_state=state)
        if status is not None:
            self._state = replace(self._state, status=status)
        self._publish_state()

    def refresh_telemetry(self, selected_node_id: UUID | None = None) -> None:
        """Collect metrics, source/MIDI statuses, and (optionally) one node's
        memory diagnostic on the task pool, then publish them as state.

        A global refresh (no selected node) is skipped while the session knows
        the engine is stopped: a stopped engine publishes no data, and the
        cheap status liveness check still catches crashes and stale heartbeats.
        """
        if self._closed:
            return
        if selected_node_id is None and self._session.is_known_stopped():
            return
        self._submit("refresh", lambda: self._load_telemetry(selected_node_id))

    def _load_telemetry(self, selected_node_id: UUID | None) -> _TelemetrySnapshot:
        metrics = self._session.metrics()
        sources = self._session.client.source_status()
        midi_outputs = self._session.client.midi_output_status()
        diagnostic = None
        if selected_node_id is not None:
            diagnostics = self._session.client.node_memory_diagnostics(selected_node_id)
            diagnostic = diagnostics[0] if diagnostics else None
        return metrics, sources, midi_outputs, diagnostic

    def _apply_telemetry(self, result: _TelemetrySnapshot) -> None:
        metrics, sources, midi_outputs, diagnostic = result
        self._state = replace(
            self._state,
            engine_state=metrics.state,
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
            return self._session.metrics(), self._session.client.node_profiles()
        except (RuntimeError, TimeoutError):
            return None, ()

    def pump_previews_once(
        self,
        *,
        note_visible: bool,
        image_visible: bool,
        view: GraphViewModel,
    ) -> None:
        """Poll the preview ports, route the batch, and hand the sink
        decisions to the compositor callback.

        Driven by the compositor's timer (which supplies the docks' Qt
        visibility flags and the current view-model projection); the engine
        session owns the per-port sequence cursors, the note family is not
        pumped while the note dock is hidden, and this step owns the
        routing: the batch is routed with the bridge's preview router before
        the compositor sees it. The image dock's visualizer-source set is
        published on the projection itself, so the tick just reads it. A
        failed poll (e.g. the engine transport closing) skips the tick
        without touching any published state.
        """
        if self._closed:
            return
        try:
            note_previews = self._session.next_note_previews() if note_visible else ()
            pumped = PumpedPreviews(
                image_previews=self._session.next_image_previews(),
                value_previews=self._session.next_value_previews(),
                note_previews=note_previews,
            )
        except (RuntimeError, TimeoutError):
            return
        self._on_previews(
            self._preview_router.route(
                pumped,
                image_visible=image_visible,
                image_dock_sources=view.derived.image_dock_source_keys,
            )
        )

    # -- runtime profiling ---------------------------------------------------

    def set_profiling_enabled(self, enabled: bool) -> None:
        self._session.client.set_profiling_enabled(enabled)

    def reset_profiling(self) -> None:
        self._session.client.reset_profiling()

    def refresh_profiles(self) -> None:
        """Poll the per-node profiles on the task pool and publish them.

        The read is one IPC round trip; running it off the UI thread keeps a
        hung child from stalling the event loop on the 250 ms profiler tick.
        """
        if self._closed:
            return
        self._submit("profiles", self._session.client.node_profiles)

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
            "devices", lambda: self._session.client.device_catalogue(force_refresh=force_refresh)
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
            if self._pending_activation is not None:
                # A newer snapshot was queued while this activation was in
                # flight; the inflight slot is free now, so flush it.
                self._flush_activation()
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
        elif kind == "profiles" and isinstance(result, tuple):
            self.publish_node_profiles(cast("tuple[NodeProfile, ...]", result))
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
        # Every publish carries the session's live known-stopped cache, so the
        # compositor's tick never reasons from a private copy of it.
        self._state = replace(self._state, known_stopped=self._session.is_known_stopped())
        self._on_state(self._state)
