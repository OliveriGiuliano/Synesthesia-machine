"""Engine-status presentation policy over the bridge's published state.

The engine bridge publishes an ``EngineBridgeState`` on the UI thread; this
presenter is the interpretation of that state for display: which settled
facts are new (object-identity dedup), which error digests changed, whether
the periodic telemetry refresh should run at all, and which crash dialog
should be shown. The window owns localization (``tr``/``trf``) and widget
application; the presenter emits structured display intents and owns the
signature/identity caches, so the policy is testable headlessly without a
window.

The module is Qt-free: ``EngineBridgeState`` in, intents out.
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineStatus,
    MidiOutputConnectionState,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NodeProfile,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.ui.engine_bridge import (
    EngineBridgeState,
    EngineRestartOutcome,
    TransportOutcome,
)

__all__ = [
    "EngineStatusIntent",
    "EngineStatusPresenter",
    "ErrorTooltip",
    "FailureIntent",
    "StateIntents",
    "StatusMessageIntent",
    "TelemetryIntents",
    "TelemetryRefresh",
    "TelemetryTooltip",
]


@dataclass(frozen=True, slots=True)
class EngineStatusIntent:
    """Display intent for one published ``EngineStatus``."""

    restart_enabled: bool
    restarting: bool


@dataclass(frozen=True, slots=True)
class FailureIntent:
    """Display intent for a crashed/unresponsive engine.

    ``dialog`` is ``False`` when the failure was already announced (same
    signature as the previous dialog); the window still renders the label
    and status-bar text from the ``EngineStatus`` it holds.
    """

    crash_report_available: bool
    dialog: bool


@dataclass(frozen=True, slots=True)
class ErrorTooltip:
    """The error lines a telemetry tooltip and status-bar message are built
    from (engine-provided text; the window localizes the frame text and the
    empty-error fallbacks)."""

    sources: tuple[tuple[UUID, str | None], ...]
    midi: tuple[tuple[UUID, str | None, tuple[str, ...]], ...]
    nodes: tuple[tuple[UUID, str, str], ...]


@dataclass(frozen=True, slots=True)
class TelemetryTooltip:
    """The telemetry figures a healthy engine's tooltip shows."""

    midi: tuple[tuple[str | None, str, int], ...]
    figures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TelemetryIntents:
    """Display intents for one new telemetry publish.

    ``tooltip`` is the live tooltip (error lines while any error digest is
    non-empty, the telemetry figures otherwise); ``announce_errors`` is set
    only when the error digests changed, which is what triggers a status-bar
    announcement.
    """

    engine_state: str
    source_states: tuple[str, ...]
    midi_states: tuple[str, ...]
    ticks: int
    dropped: int
    tooltip: ErrorTooltip | TelemetryTooltip
    announce_errors: bool
    diagnostic: NodeMemoryDiagnostic | None
    source_statuses: tuple[SourceStatus, ...]
    midi_outputs: tuple[MidiOutputStatus, ...]


@dataclass(frozen=True, slots=True)
class StateIntents:
    """The display intents one published state produces (each ``None`` when
    the corresponding fact is unchanged by object identity)."""

    telemetry: TelemetryIntents | None = None
    device_catalogue: DeviceCatalogue | None = None
    node_profiles: tuple[NodeProfile, ...] | None = None
    activation: EngineActivation | None = None
    transport: TransportOutcome | None = None
    restart: EngineRestartOutcome | None = None


@dataclass(frozen=True, slots=True)
class StatusMessageIntent:
    """Display intent for one engine status message key."""

    key: str
    timeout_ms: int
    needs_status_refresh: bool


@dataclass(frozen=True, slots=True)
class TelemetryRefresh:
    """The periodic refresh plan: ``node`` is the exactly-one selection or
    ``None`` for an engine-wide read."""

    skip: bool
    node: UUID | None = None


class EngineStatusPresenter:
    """Interprets published engine state into display intents.

    Owns the signature caches (error digests, failure signature) and the
    object-identity caches that distinguish a new settled fact from a stale
    one carried along by an unrelated publish.
    """

    def __init__(self) -> None:
        self._failure_signature: tuple[object, ...] | None = None
        self._source_error_signature: tuple[tuple[UUID, str | None], ...] = ()
        self._midi_error_signature: tuple[tuple[UUID, str | None, tuple[str, ...]], ...] = ()
        self._runtime_error_signature: tuple[tuple[UUID, str, str], ...] = ()
        self._rendered_metrics: EngineMetrics | None = None
        self._applied_device_catalogue: DeviceCatalogue | None = None
        self._rendered_profiles: tuple[NodeProfile, ...] | None = None
        self._rendered_last_activation: EngineActivation | None = None
        self._rendered_last_transport: TransportOutcome | None = None
        self._rendered_last_restart: EngineRestartOutcome | None = None

    # -- identity-deduped state fan-out ------------------------------------

    def present_state(self, state: EngineBridgeState) -> StateIntents:
        """Derive the display intents of one published state.

        The ``last_*`` fields persist in the published state; object identity
        tells a new settled result from a stale one carried along by an
        unrelated publish.
        """
        telemetry: TelemetryIntents | None = None
        if state.metrics is not None and state.metrics is not self._rendered_metrics:
            self._rendered_metrics = state.metrics
            telemetry = self._telemetry_intents(state)
        device_catalogue: DeviceCatalogue | None = None
        if (
            state.device_catalogue is not None
            and state.device_catalogue is not self._applied_device_catalogue
        ):
            self._applied_device_catalogue = state.device_catalogue
            device_catalogue = state.device_catalogue
        node_profiles: tuple[NodeProfile, ...] | None = None
        if state.node_profiles is not self._rendered_profiles:
            self._rendered_profiles = state.node_profiles
            node_profiles = state.node_profiles
        activation: EngineActivation | None = None
        if (
            state.last_activation is not None
            and state.last_activation is not self._rendered_last_activation
        ):
            self._rendered_last_activation = state.last_activation
            activation = state.last_activation
        transport: TransportOutcome | None = None
        if (
            state.last_transport is not None
            and state.last_transport is not self._rendered_last_transport
        ):
            self._rendered_last_transport = state.last_transport
            transport = state.last_transport
        restart: EngineRestartOutcome | None = None
        if state.last_restart is not None and state.last_restart is not self._rendered_last_restart:
            self._rendered_last_restart = state.last_restart
            restart = state.last_restart
        return StateIntents(
            telemetry=telemetry,
            device_catalogue=device_catalogue,
            node_profiles=node_profiles,
            activation=activation,
            transport=transport,
            restart=restart,
        )

    def _telemetry_intents(self, state: EngineBridgeState) -> TelemetryIntents:
        metrics = state.metrics
        assert metrics is not None
        sources = state.source_statuses
        midi_outputs = state.midi_output_statuses
        source_errors = tuple(
            (status.node_id, status.last_error)
            for status in sources
            if status.state is SourceState.ERROR
        )
        midi_errors = tuple(
            (
                status.node_id,
                status.last_error,
                status.available_ports,
            )
            for status in midi_outputs
            if status.connection_state
            in {MidiOutputConnectionState.ERROR, MidiOutputConnectionState.UNAVAILABLE}
        )
        runtime_errors = tuple(
            (error.node_id, error.code, error.message) for error in metrics.runtime_errors
        )
        digest_changed = (
            source_errors != self._source_error_signature
            or midi_errors != self._midi_error_signature
            or runtime_errors != self._runtime_error_signature
        )
        if source_errors or midi_errors or runtime_errors:
            tooltip = ErrorTooltip(sources=source_errors, midi=midi_errors, nodes=runtime_errors)
        else:
            tooltip = TelemetryTooltip(
                midi=tuple(
                    (
                        status.selected_port or None,
                        str(status.connection_state.value),
                        status.active_note_count,
                    )
                    for status in midi_outputs
                ),
                figures=(
                    f"{metrics.input_fps:.1f}",
                    f"{metrics.processed_fps:.1f}",
                    f"{metrics.preview_fps:.1f}",
                    f"{metrics.p95_graph_execution_ms:.2f}",
                    f"{metrics.mailbox_occupancy}/{metrics.mailbox_capacity}",
                    f"{metrics.frame_age_ms:.1f}",
                    f"{metrics.supervisor.cpu_percent:.1f}",
                    f"{metrics.memory_bytes / (1024 * 1024):.1f}",
                ),
            )
        self._source_error_signature = source_errors
        self._midi_error_signature = midi_errors
        self._runtime_error_signature = runtime_errors
        return TelemetryIntents(
            engine_state=str(metrics.state.value),
            source_states=tuple(str(status.state.value) for status in sources),
            midi_states=tuple(str(status.connection_state.value) for status in midi_outputs),
            ticks=metrics.processed_ticks,
            dropped=metrics.dropped_before_processing,
            tooltip=tooltip,
            announce_errors=bool(
                (source_errors or midi_errors or runtime_errors) and digest_changed
            ),
            diagnostic=state.diagnostic,
            source_statuses=sources,
            midi_outputs=midi_outputs,
        )

    # -- engine status and failure ------------------------------------------

    def present_engine_status(self, status: EngineStatus) -> EngineStatusIntent:
        failed = status.connection_state in {
            EngineConnectionState.CRASHED,
            EngineConnectionState.UNRESPONSIVE,
        }
        return EngineStatusIntent(
            restart_enabled=failed,
            restarting=status.connection_state is EngineConnectionState.RESTARTING,
        )

    def present_engine_failure(self, status: EngineStatus) -> FailureIntent:
        """The crash dialog decision: the window renders the label and
        status-bar text from the ``EngineStatus`` it holds and shows the
        critical dialog only when ``dialog`` is set (a new failure
        signature)."""
        signature = (
            status.connection_state,
            status.child_process_id,
            status.exit_code,
            status.last_error,
            status.crash_log_path,
        )
        dialog = False
        if signature != self._failure_signature:
            self._failure_signature = signature
            dialog = True
        crash_report_available = (
            status.crash_log_path is not None and Path(status.crash_log_path).is_file()
        )
        return FailureIntent(crash_report_available=crash_report_available, dialog=dialog)

    def failure_reset(self) -> None:
        """Forget the announced failure after the engine restarted
        successfully, so a later failure re-announces."""
        self._failure_signature = None

    # -- status messages -----------------------------------------------------

    def present_status_message(self, message: str, timeout_ms: int) -> StatusMessageIntent | None:
        """Map a status message key to its display intent.

        ``activated``/``activation_rejected`` are rendered from the published
        state, not as status messages; ``restart_failed`` additionally needs
        a status refresh because the task failure changed engine run state.
        """
        if message in {"activated", "activation_rejected"}:
            return None
        if message not in {
            "panic_ok",
            "activation_failed",
            "devices_refreshing",
            "devices_failed",
            "transport_failed",
            "panic_failed",
            "restart_failed",
        }:
            return None
        return StatusMessageIntent(
            key=message,
            timeout_ms=timeout_ms,
            needs_status_refresh=message == "restart_failed",
        )

    # -- periodic refresh plan ------------------------------------------------

    def plan_telemetry_refresh(
        self,
        state: EngineBridgeState,
        selected_node_ids: Set[UUID],
    ) -> TelemetryRefresh:
        """Whether the periodic tick should issue a telemetry round trip.

        A stopped engine publishes no data and the cheap liveness check still
        catches crash and heartbeat timeouts, so a known-stopped engine with
        no exactly-one selection skips the round trip.
        """
        status = state.status
        if status is None or status.connection_state is EngineConnectionState.RESTARTING:
            return TelemetryRefresh(skip=True)
        if status.connection_state in {
            EngineConnectionState.CRASHED,
            EngineConnectionState.UNRESPONSIVE,
        }:
            return TelemetryRefresh(skip=True)
        selected_node = next(iter(selected_node_ids)) if len(selected_node_ids) == 1 else None
        if (
            state.known_stopped
            and status.connection_state is EngineConnectionState.CONNECTED
            and selected_node is None
        ):
            return TelemetryRefresh(skip=True)
        return TelemetryRefresh(skip=False, node=selected_node)
