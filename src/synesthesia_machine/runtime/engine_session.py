"""Headless engine session: client-side bookkeeping over the EngineClient.

The :class:`EngineSession` facade owns everything a caller would otherwise
have to track by hand when driving the engine: a cursor-free preview pump
(the client keeps the per-port sequence cursors), the six-state connection
machine (the client reports the liveness state it folded from heartbeats),
the "engine is known stopped" cache (the cheap liveness skip used by
periodic UI ticks), and the restart-outcome mapping.  The session exposes
only behaviours of its own; every other ``EngineClient`` protocol method is
reachable through :attr:`EngineSession.client`, so the protocol surface
lives in exactly one place.  It depends only on the ``EngineClient``
protocol, so it behaves identically over the process and in-process
clients, and it is Qt-free: the UI layer builds on it via the
``EngineBridge``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from synesthesia_machine.contracts import (
    EngineActivation,
    EngineClient,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    NotePreview,
    ValuePreview,
)

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot

__all__ = ["EngineSession", "RestartOutcome"]


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

_RESTART_REASONS = ("ok", "rejected", "restart_failed")


@dataclass(frozen=True, slots=True)
class RestartOutcome:
    """A completed engine restart: ``ok``, ``rejected``, or
    ``restart_failed``; the failure outcomes carry the exception detail so
    a UI can surface it."""

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

    The session's genuine behaviours sit on top of the protocol: the
    cursor-free preview pump with per-family cursor resets, the known-stopped
    cache and its invalidation, the restart-outcome mapping, and the
    demand-roots callback for activations.  Everything else of the client
    surface is one hop away through :attr:`client`, so a reader of the
    protocol file and of this file sees the same truth about which method
    lives where.
    """

    def __init__(
        self,
        client: EngineClient,
        *,
        demand_roots_for: Callable[[GraphSnapshot], tuple[UUID, ...]] | None = None,
    ) -> None:
        self._client = client
        self._demand_roots_for = demand_roots_for
        self._known_stopped = False
        self._closed = False

    @property
    def client(self) -> EngineClient:
        """The underlying client: the full protocol surface, one hop away."""
        return self._client

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
        if self._closed:
            return
        self._client.reset_image_preview_cursors()

    def reset_note_preview_cursors(self) -> None:
        """Make the client re-serve every retained note preview (e.g. the note dock was hidden)."""
        if self._closed:
            return
        self._client.reset_note_preview_cursors()

    # -- connection state --------------------------------------------------

    def poll_status(self) -> tuple[EngineConnectionState, EngineStatus | None]:
        """One status round trip.

        Returns the connection state and the raw status.  The state is
        exactly what the client folded from its own liveness: the process
        adapter folds a stale heartbeat into UNRESPONSIVE and a lost child
        into CRASHED, and both adapters fold a closed client into CLOSED, so
        the session applies no second fold.  The status is ``None`` only for
        a closed session, which polls nothing; failure and restart states
        drop the known-stopped cache so the next tick re-learns the real
        state.
        """
        if self._closed:
            return EngineConnectionState.CLOSED, None
        status = self._client.status()
        state = status.connection_state
        if state in _STOP_CACHE_DROP_STATES:
            self._known_stopped = False
        return state, status

    def connection_state(self) -> EngineConnectionState:
        """The connection state the client folded (see :meth:`poll_status`)."""
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

        The client resets its own preview state on every activation outcome,
        because a replaced or stopped runtime re-serves sequences from
        scratch.  Restart-path graph memory lives in the adapter, not here.
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
        return activation

    def restart(self) -> RestartOutcome:
        """Restart the engine and map the adapter's outcome.

        The adapter re-activates its own last accepted graph (each transport
        owns that memory) and returns the resulting activation; the session
        only maps it: a failure of the process replacement is
        ``restart_failed``, a rejected rebuild is ``rejected``, otherwise
        ``ok``.  A successful restart resets the client's preview state (the
        new engine generation re-serves sequences from scratch); a failed
        restart keeps the old engine's preview state intact.
        """
        self._known_stopped = False
        try:
            activation = self._client.restart()
        except (RuntimeError, TimeoutError) as error:
            return RestartOutcome(ok=False, reason="restart_failed", detail=str(error))
        if activation is not None and not activation.activated:
            return RestartOutcome(ok=False, reason="rejected")
        return RestartOutcome(ok=True, reason="ok")

    # -- telemetry ---------------------------------------------------------

    def metrics(self) -> EngineMetrics:
        metrics = self._client.metrics()
        self._known_stopped = metrics.state is EngineState.STOPPED
        return metrics

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the session and the underlying client (idempotent)."""
        if self._closed:
            return
        self._closed = True
        self._known_stopped = False
        self._client.close()
