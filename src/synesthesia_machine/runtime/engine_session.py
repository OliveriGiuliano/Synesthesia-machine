"""Headless engine session: client-side bookkeeping over the EngineClient.

The :class:`EngineSession` facade owns everything a caller would otherwise
have to track by hand when driving the engine: a cursor-free preview pump
(the client keeps the per-port sequence cursors), the folded six-state
connection machine, the "engine is known stopped" cache (the cheap
liveness skip used by periodic UI ticks), and the last valid activation
used by the restart path.  It depends only on the
``EngineClient`` protocol, so it behaves identically over the process and
in-process clients, and it is Qt-free: the UI layer builds on it via the
``EngineBridge``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineClient,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NodeProfile,
    NotePreview,
    SourceStatus,
    ValuePreview,
)

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot

__all__ = ["EngineSession", "RestartOutcome"]

# Mirrors the process client's DEFAULT_HEARTBEAT_TIMEOUT_S: a client that
# self-polices its heartbeat (the process client) already folds this into
# status(), so the check mainly covers clients that publish heartbeat
# timestamps without timing themselves out.
_HEARTBEAT_TIMEOUT_S = 2.0

#: States in which a previously observed STOPPED engine state can no longer
#: be trusted: the engine is failing, restarting, or gone, so the next
#: telemetry tick must re-learn the real state.
_STOP_CACHE_DROP_STATES = frozenset(
    {
        EngineConnectionState.CRASHED,
        EngineConnectionState.UNRESPONSIVE,
        EngineConnectionState.RESTARTING,
        EngineConnectionState.CLOSED,
    }
)

_RESTART_REASONS = ("ok", "rejected", "restart_failed", "rebuild_failed")


@dataclass(frozen=True, slots=True)
class RestartOutcome:
    """A completed engine restart: ``ok``, ``rejected``, ``restart_failed``,
    or ``rebuild_failed``; the two failure outcomes carry the exception
    detail so a UI can surface it."""

    ok: bool
    reason: str = "ok"
    detail: str = ""

    def __post_init__(self) -> None:
        if self.reason not in _RESTART_REASONS:
            msg = f"restart reason must be one of {_RESTART_REASONS}"
            raise ValueError(msg)
        if self.ok is not (self.reason == "ok"):
            msg = "restart ok flag must match its reason"
            raise ValueError(msg)


class EngineSession:
    """Drives an engine through the ``EngineClient`` protocol.

    Preview polling is cursor-free: the client owns the per-port sequence
    cursors, so ``next_*_previews`` returns only previews newer than the last
    delivered batch and a caller cannot poll with a stale threshold.
    ``connection_state`` folds the client's status into the six-state machine
    (closed session, unreachable client, stale heartbeat), and
    ``is_known_stopped`` carries the "engine is stopped, don't re-poll"
    optimization.  Transport and status calls are plain pass-throughs.
    """

    def __init__(
        self,
        client: EngineClient,
        *,
        demand_roots_for: Callable[[GraphSnapshot], tuple[UUID, ...]] | None = None,
    ) -> None:
        self._client = client
        self._demand_roots_for = demand_roots_for
        # Last valid (accepted) activation, remembered for the restart path.
        self._last_snapshot: GraphSnapshot | None = None
        self._last_demand_roots: tuple[UUID, ...] | None = None
        self._known_stopped = False
        self._closed = False

    # -- preview pump ------------------------------------------------------

    def next_image_previews(self) -> tuple[ImagePreview, ...]:
        """Deliver the previews the client has not yet served (cursors live in the client)."""
        if self._closed:
            return ()
        return self._client.next_image_previews()

    def next_value_previews(self) -> tuple[ValuePreview, ...]:
        """Deliver the value previews the client has not yet served."""
        if self._closed:
            return ()
        return self._client.next_value_previews()

    def next_note_previews(self) -> tuple[NotePreview, ...]:
        """Deliver the note previews the client has not yet served."""
        if self._closed:
            return ()
        return self._client.next_note_previews()

    def reset_image_preview_cursors(self) -> None:
        """Make the client re-serve every retained image preview (dock hidden)."""
        self._client.reset_image_preview_cursors()

    def reset_note_preview_cursors(self) -> None:
        """Make the client re-serve every retained note preview (e.g. the note dock was hidden)."""
        self._client.reset_note_preview_cursors()

    # -- connection state --------------------------------------------------

    def poll_status(self) -> tuple[EngineConnectionState, EngineStatus | None]:
        """One status round trip folded into the six-state connection machine.

        Returns the folded connection state and the raw status; the status is
        ``None`` when a closed session or an unreachable client (an IPC
        error) folded the state to CLOSED/CRASHED, and a client that *reports*
        a crash keeps its status (exit code, crash log path) so the UI can
        render it.  A closed session reports CLOSED without polling; a
        CONNECTED engine whose last heartbeat is stale folds to UNRESPONSIVE.
        Failure and restart states drop the known-stopped cache so the next
        tick re-learns the real state.
        """
        if self._closed:
            return EngineConnectionState.CLOSED, None
        try:
            status = self._client.status()
        except (RuntimeError, TimeoutError):
            status = None
            state = EngineConnectionState.CRASHED
        else:
            state = status.connection_state
            if (
                state is EngineConnectionState.CONNECTED
                and status.last_heartbeat_monotonic_ns is not None
                and (
                    time.monotonic_ns() - status.last_heartbeat_monotonic_ns
                    > _HEARTBEAT_TIMEOUT_S * 1_000_000_000
                )
            ):
                state = EngineConnectionState.UNRESPONSIVE
        if state in _STOP_CACHE_DROP_STATES:
            self._known_stopped = False
        return state, status

    def connection_state(self) -> EngineConnectionState:
        """The folded six-state connection state (see :meth:`poll_status`)."""
        return self.poll_status()[0]

    def is_known_stopped(self) -> bool:
        """Whether the engine is known to be stopped ("don't re-poll" cache).

        True only after a ``metrics`` read reports ``EngineState.STOPPED``;
        transport commands, activations, restarts, and failure states drop
        it.  A periodic tick may skip the metrics round trip while this is
        True and the connection is still CONNECTED.
        """
        return self._known_stopped

    # -- transport ---------------------------------------------------------

    def play(self, source_node_id: UUID | None = None) -> None:
        self._known_stopped = False
        self._client.play(source_node_id)

    def pause(self, source_node_id: UUID | None = None) -> None:
        self._known_stopped = False
        self._client.pause(source_node_id)

    def resume(self, source_node_id: UUID | None = None) -> None:
        self._known_stopped = False
        self._client.resume(source_node_id)

    def stop(self, source_node_id: UUID | None = None) -> None:
        self._known_stopped = False
        self._client.stop(source_node_id)

    def reload(self, source_node_id: UUID | None = None) -> None:
        self._known_stopped = False
        self._client.reload(source_node_id)

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        self._known_stopped = False
        self._client.seek(source_node_id, source_time_s)

    def panic(self) -> None:
        self._known_stopped = False
        self._client.panic()

    # -- activation / restart ----------------------------------------------

    def activate(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
    ) -> EngineActivation:
        """Activate a graph, computing demand roots via the callback when
        ``demand_roots`` is not given.

        An accepted activation becomes the last valid state remembered for
        the restart path; rejections keep the previous one. The client resets
        its own preview state on every activation outcome, because a replaced
        or stopped runtime re-serves sequences from scratch.
        """
        roots: tuple[UUID, ...] | None
        if demand_roots is not None:
            roots = tuple(demand_roots)
        elif self._demand_roots_for is not None:
            roots = self._demand_roots_for(snapshot)
        else:
            roots = None
        self._known_stopped = False
        activation = self._client.activate(snapshot, demand_roots=roots)
        if activation.activated:
            self._last_snapshot = snapshot
            self._last_demand_roots = roots
        return activation

    def restart(self) -> RestartOutcome:
        """Restart the engine, rebuilding the last valid graph if needed.

        The adapters re-activate their own last accepted graph and return
        its activation; when the client returns none (no valid graph on the
        engine side) and the session remembers a valid one, the session
        re-activates it with the remembered demand roots.  A successful
        restart resets the client's preview state (the new engine generation
        re-serves sequences from scratch); a failed restart (the process
        could not be replaced) keeps the old engine's preview state intact.
        """
        self._known_stopped = False
        try:
            activation = self._client.restart()
        except (RuntimeError, TimeoutError) as error:
            return RestartOutcome(ok=False, reason="restart_failed", detail=str(error))
        if activation is None and self._last_snapshot is not None:
            try:
                activation = self._client.activate(
                    self._last_snapshot, demand_roots=self._last_demand_roots
                )
            except (RuntimeError, TimeoutError) as error:
                return RestartOutcome(ok=False, reason="rebuild_failed", detail=str(error))
        if activation is not None and not activation.activated:
            return RestartOutcome(ok=False, reason="rejected")
        return RestartOutcome(ok=True, reason="ok")

    # -- status / telemetry pass-throughs ----------------------------------

    def status(self) -> EngineStatus:
        return self._client.status()

    def metrics(self) -> EngineMetrics:
        metrics = self._client.metrics()
        self._known_stopped = metrics.state is EngineState.STOPPED
        return metrics

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        return self._client.source_status(source_node_id)

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        return self._client.midi_output_status(output_node_id)

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        return self._client.device_catalogue(force_refresh=force_refresh)

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        return self._client.node_memory_diagnostics(node_id)

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        return self._client.node_profiles()

    def set_profiling_enabled(self, enabled: bool) -> None:
        self._client.set_profiling_enabled(enabled)

    def reset_profiling(self) -> None:
        self._client.reset_profiling()

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        return self._client.wait_until_idle(timeout_s)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the session and the underlying client (idempotent)."""
        if self._closed:
            return
        self._closed = True
        self._known_stopped = False
        self._client.close()
