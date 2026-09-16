"""Headless EngineSession facade tests over a recording fake client.

The session depends only on the ``EngineClient`` protocol, so every test
drives it through a fake: no process, no Qt, no engine internals.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import numpy as np
import pytest

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
    NoteActivity,
    NotePreview,
    SourceStatus,
    ValuePreview,
    freeze_uint8_preview,
)
from synesthesia_machine.graph import GraphSnapshot, ValidationReport
from synesthesia_machine.runtime import EngineSession, RestartOutcome

SOURCE_A = UUID("00000000-0000-0000-0000-0000000000a1")
SOURCE_B = UUID("00000000-0000-0000-0000-0000000000b2")


def _snapshot_roots_list() -> list[tuple[GraphSnapshot, tuple[UUID, ...] | None]]:
    return []


def _verb_list() -> list[str]:
    return []


def _seek_args_list() -> list[tuple[UUID, float]]:
    return []


def _port_cursor_list() -> list[Mapping[tuple[UUID, str], int] | None]:
    return []


def _owner_cursor_list() -> list[Mapping[UUID, int] | None]:
    return []


@dataclass(slots=True)
class _FakeEngineClient:
    """Recording EngineClient: canned status/previews, logged calls."""

    engine_state: EngineState = EngineState.STOPPED
    connection_state: EngineConnectionState = EngineConnectionState.CONNECTED
    activation_activated: bool = True
    restart_result: EngineActivation | None = None
    image_previews: tuple[ImagePreview, ...] = ()
    value_previews: tuple[ValuePreview, ...] = ()
    note_previews: tuple[NotePreview, ...] = ()
    status_error: Exception | None = None
    restart_error: Exception | None = None
    activate_error: Exception | None = None
    idle: bool = True
    last_heartbeat_offset_s: float = 0.0
    activations: list[tuple[GraphSnapshot, tuple[UUID, ...] | None]] = field(
        default_factory=_snapshot_roots_list
    )
    calls: list[str] = field(default_factory=_verb_list)
    seek_args: list[tuple[UUID, float]] = field(default_factory=_seek_args_list)
    image_cursors: list[Mapping[tuple[UUID, str], int] | None] = field(
        default_factory=_port_cursor_list
    )
    value_cursors: list[Mapping[tuple[UUID, str], int] | None] = field(
        default_factory=_port_cursor_list
    )
    note_cursors: list[Mapping[UUID, int] | None] = field(default_factory=_owner_cursor_list)
    closed: bool = False

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        self.calls.append("activate")
        error = self.activate_error
        if error is not None:
            raise error
        self.activations.append((snapshot, None if demand_roots is None else tuple(demand_roots)))
        return EngineActivation(snapshot.revision, ValidationReport(), self.activation_activated)

    def play(self, source_node_id: UUID | None = None) -> None:
        del source_node_id
        self.calls.append("play")

    def pause(self, source_node_id: UUID | None = None) -> None:
        del source_node_id
        self.calls.append("pause")

    def resume(self, source_node_id: UUID | None = None) -> None:
        del source_node_id
        self.calls.append("resume")

    def stop(self, source_node_id: UUID | None = None) -> None:
        del source_node_id
        self.calls.append("stop")

    def reload(self, source_node_id: UUID | None = None) -> None:
        del source_node_id
        self.calls.append("reload")

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        self.calls.append("seek")
        self.seek_args.append((source_node_id, source_time_s))

    def panic(self) -> None:
        self.calls.append("panic")

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        del source_node_id
        self.calls.append("source_status")
        return ()

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        del output_node_id
        self.calls.append("midi_output_status")
        return ()

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        del force_refresh
        self.calls.append("device_catalogue")
        return DeviceCatalogue()

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        del node_id
        self.calls.append("node_memory_diagnostics")
        return ()

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        self.calls.append("node_profiles")
        return ()

    def set_profiling_enabled(self, enabled: bool) -> None:
        del enabled
        self.calls.append("set_profiling_enabled")

    def reset_profiling(self) -> None:
        self.calls.append("reset_profiling")

    def metrics(self) -> EngineMetrics:
        self.calls.append("metrics")
        return EngineMetrics(self.engine_state)

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        self.calls.append("poll_image_previews")
        self.image_cursors.append(None if after_sequences is None else dict(after_sequences))
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.image_previews
            if preview.sequence > thresholds.get((preview.owner_id, preview.source_port_id), 0)
        )

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        self.calls.append("poll_note_previews")
        self.note_cursors.append(None if after_sequences is None else dict(after_sequences))
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.note_previews
            if preview.sequence > thresholds.get(preview.owner_id, 0)
        )

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        self.calls.append("poll_value_previews")
        self.value_cursors.append(None if after_sequences is None else dict(after_sequences))
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.value_previews
            if preview.sequence > thresholds.get((preview.owner_id, preview.source_port_id), 0)
        )

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        self.calls.append("wait_until_idle")
        return self.idle

    def status(self) -> EngineStatus:
        self.calls.append("status")
        error = self.status_error
        if error is not None:
            raise error
        heartbeat: int | None = None
        if self.connection_state is EngineConnectionState.CONNECTED:
            heartbeat = time.monotonic_ns() - int(self.last_heartbeat_offset_s * 1_000_000_000)
        return EngineStatus(self.connection_state, last_heartbeat_monotonic_ns=heartbeat)

    def restart(self) -> EngineActivation | None:
        self.calls.append("restart")
        error = self.restart_error
        if error is not None:
            raise error
        return self.restart_result

    def clear_previews(self) -> None:
        self.calls.append("clear_previews")

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


def _snapshot(revision: int = 1) -> GraphSnapshot:
    return GraphSnapshot(document_id=uuid4(), revision=revision, nodes=(), connections=())


def _image_preview(owner: UUID, port: str, sequence: int) -> ImagePreview:
    data = freeze_uint8_preview(np.zeros((2, 2, 3), dtype=np.uint8))
    return ImagePreview(
        owner_id=owner,
        source_port_id=port,
        sequence=sequence,
        tick_index=sequence,
        width=2,
        height=2,
        channels=3,
        data=data,
    )


def _value_preview(owner: UUID, port: str, sequence: int) -> ValuePreview:
    return ValuePreview(
        owner_id=owner,
        source_port_id=port,
        sequence=sequence,
        tick_index=sequence,
        port_type="INT",
        text="42",
    )


def _note_preview(owner: UUID, sequence: int) -> NotePreview:
    return NotePreview(
        owner_id=owner,
        sequence=sequence,
        tick_index=sequence,
        notes=(NoteActivity(0, 60, 1),),
    )


def _activation(revision: int = 1, *, activated: bool = True) -> EngineActivation:
    return EngineActivation(revision, ValidationReport(), activated)


def test_next_image_previews_delivers_batch_once_and_advances_cursors() -> None:
    client = _FakeEngineClient(
        image_previews=(
            _image_preview(SOURCE_A, "output", 1),
            _image_preview(SOURCE_A, "output", 2),
            _image_preview(SOURCE_B, "output", 1),
        )
    )
    session = EngineSession(client)
    first = session.next_image_previews()
    assert [preview.sequence for preview in first] == [1, 2, 1]
    # The session passes its own cursors; the test itself never passes
    # after_sequences.  The first poll starts empty...
    assert client.image_cursors[0] == {}
    assert session.next_image_previews() == ()
    # ...and the second poll sees the advanced cursors.
    assert client.image_cursors[1] == {
        (SOURCE_A, "output"): 2,
        (SOURCE_B, "output"): 1,
    }


def test_next_value_previews_advances_cursors_per_port() -> None:
    client = _FakeEngineClient(
        value_previews=(
            _value_preview(SOURCE_A, "count", 1),
            _value_preview(SOURCE_A, "count", 3),
            _value_preview(SOURCE_B, "count", 1),
        )
    )
    session = EngineSession(client)
    assert [preview.sequence for preview in session.next_value_previews()] == [1, 3, 1]
    assert session.next_value_previews() == ()
    assert client.value_cursors[1] == {
        (SOURCE_A, "count"): 3,
        (SOURCE_B, "count"): 1,
    }


def test_next_note_previews_advances_cursors_per_owner() -> None:
    client = _FakeEngineClient(
        note_previews=(
            _note_preview(SOURCE_A, 1),
            _note_preview(SOURCE_A, 2),
            _note_preview(SOURCE_B, 1),
        )
    )
    session = EngineSession(client)
    assert [preview.sequence for preview in session.next_note_previews()] == [1, 2, 1]
    assert session.next_note_previews() == ()
    assert client.note_cursors[1] == {SOURCE_A: 2, SOURCE_B: 1}


def test_clear_preview_cursors_reserves_previews() -> None:
    client = _FakeEngineClient(image_previews=(_image_preview(SOURCE_A, "output", 1),))
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    assert session.next_image_previews() == ()
    session.clear_preview_cursors()
    assert len(session.next_image_previews()) == 1


def test_connection_state_folds_client_states() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    client.connection_state = EngineConnectionState.STARTING
    assert session.connection_state() is EngineConnectionState.STARTING
    client.connection_state = EngineConnectionState.CONNECTED
    assert session.connection_state() is EngineConnectionState.CONNECTED
    client.connection_state = EngineConnectionState.UNRESPONSIVE
    assert session.connection_state() is EngineConnectionState.UNRESPONSIVE
    client.connection_state = EngineConnectionState.CRASHED
    assert session.connection_state() is EngineConnectionState.CRASHED
    client.connection_state = EngineConnectionState.RESTARTING
    assert session.connection_state() is EngineConnectionState.RESTARTING


def test_connection_state_folds_stale_heartbeat_to_unresponsive() -> None:
    client = _FakeEngineClient(last_heartbeat_offset_s=5.0)  # past the 2 s timeout
    session = EngineSession(client)
    assert session.connection_state() is EngineConnectionState.UNRESPONSIVE
    client.last_heartbeat_offset_s = 0.5
    assert session.connection_state() is EngineConnectionState.CONNECTED


def test_connection_state_folds_unreachable_client_to_crashed() -> None:
    client = _FakeEngineClient(status_error=RuntimeError("engine gone"))
    session = EngineSession(client)
    assert session.connection_state() is EngineConnectionState.CRASHED


def test_closed_session_reports_closed_without_polling() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    session.close()
    assert client.closed is True
    assert session.connection_state() is EngineConnectionState.CLOSED
    assert session.next_image_previews() == ()
    assert "status" not in client.calls


def test_known_stopped_tracks_engine_state_and_invalidates() -> None:
    client = _FakeEngineClient(engine_state=EngineState.STOPPED)
    session = EngineSession(client)
    assert session.is_known_stopped() is False  # unknown until first metrics
    session.metrics()
    assert session.is_known_stopped() is True
    session.play()
    assert session.is_known_stopped() is False
    client.engine_state = EngineState.RUNNING
    session.metrics()
    assert session.is_known_stopped() is False
    client.engine_state = EngineState.STOPPED
    session.metrics()
    assert session.is_known_stopped() is True
    # A failing connection drops the cache so the next tick re-learns.
    client.connection_state = EngineConnectionState.CRASHED
    assert session.connection_state() is EngineConnectionState.CRASHED
    assert session.is_known_stopped() is False
    client.connection_state = EngineConnectionState.CONNECTED
    session.metrics()
    assert session.is_known_stopped() is True
    session.close()
    assert session.is_known_stopped() is False


def test_activate_computes_demand_roots_via_callback() -> None:
    client = _FakeEngineClient()
    seen: list[GraphSnapshot] = []

    def roots_for(snapshot: GraphSnapshot) -> tuple[UUID, ...]:
        seen.append(snapshot)
        return (SOURCE_A,)

    session = EngineSession(client, demand_roots_for=roots_for)
    snapshot = _snapshot()
    activation = session.activate(snapshot)
    assert activation.activated is True
    assert client.activations == [(snapshot, (SOURCE_A,))]
    assert seen == [snapshot]

    explicit = _snapshot(revision=2)
    session.activate(explicit, demand_roots=(SOURCE_B,))
    assert client.activations[-1] == (explicit, (SOURCE_B,))
    assert seen == [snapshot]  # the callback is not consulted for explicit roots


def test_activate_without_callback_passes_no_roots() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    snapshot = _snapshot()
    session.activate(snapshot)
    assert client.activations == [(snapshot, None)]


def test_restart_uses_client_reactivation_when_available() -> None:
    client = _FakeEngineClient(restart_result=_activation())
    session = EngineSession(client)
    assert session.restart() == RestartOutcome(True, "ok")
    assert client.calls == ["restart"]
    assert client.activations == []  # the client re-activated internally


def test_restart_rebuilds_last_valid_graph_with_remembered_roots() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client, demand_roots_for=lambda snapshot: (SOURCE_A,))
    snapshot = _snapshot()
    session.activate(snapshot)
    client.restart_result = None  # client has no valid graph: session rebuilds
    assert session.restart() == RestartOutcome(True, "ok")
    assert client.activations == [(snapshot, (SOURCE_A,)), (snapshot, (SOURCE_A,))]


def test_restart_without_valid_graph_is_ok_and_rebuilds_nothing() -> None:
    client = _FakeEngineClient(restart_result=None)
    session = EngineSession(client)
    assert session.restart() == RestartOutcome(True, "ok")
    assert client.calls == ["restart"]


def test_restart_reports_rejected_graph() -> None:
    client = _FakeEngineClient(restart_result=_activation(activated=False))
    session = EngineSession(client)
    assert session.restart() == RestartOutcome(False, "rejected")


def test_restart_reports_restart_failure() -> None:
    client = _FakeEngineClient(restart_error=RuntimeError("spawn failed"))
    session = EngineSession(client)
    assert session.restart() == RestartOutcome(False, "restart_failed")


def test_restart_reports_rebuild_failure() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client, demand_roots_for=lambda snapshot: (SOURCE_A,))
    session.activate(_snapshot())
    client.restart_result = None
    client.activate_error = RuntimeError("ipc broken")
    assert session.restart() == RestartOutcome(False, "rebuild_failed")


def test_restart_resets_preview_cursors_on_replaced_engine() -> None:
    client = _FakeEngineClient(
        image_previews=(_image_preview(SOURCE_A, "output", 1),),
        restart_result=_activation(),
    )
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    assert session.next_image_previews() == ()
    assert session.restart() == RestartOutcome(True, "ok")
    # The child was replaced: its preview sequences restart from scratch, so
    # the session's cursors must not suppress the new frames.
    assert len(session.next_image_previews()) == 1


def test_failed_restart_keeps_preview_cursors() -> None:
    client = _FakeEngineClient(
        image_previews=(_image_preview(SOURCE_A, "output", 1),),
        restart_error=RuntimeError("spawn failed"),
    )
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    assert session.restart() == RestartOutcome(False, "restart_failed")
    # The old engine still owns its preview state: cursors stay advanced.
    assert session.next_image_previews() == ()


def test_clear_previews_clears_engine_store_and_cursors() -> None:
    client = _FakeEngineClient(image_previews=(_image_preview(SOURCE_A, "output", 1),))
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    assert session.next_image_previews() == ()
    session.clear_previews()
    assert "clear_previews" in client.calls
    assert len(session.next_image_previews()) == 1


def test_transport_commands_delegate_to_client() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    session.play(SOURCE_A)
    session.pause(SOURCE_A)
    session.resume(SOURCE_B)
    session.stop()
    session.reload(SOURCE_B)
    session.seek(SOURCE_A, 1.5)
    session.panic()
    assert client.calls == ["play", "pause", "resume", "stop", "reload", "seek", "panic"]
    assert client.seek_args == [(SOURCE_A, 1.5)]


def test_status_and_telemetry_pass_throughs_delegate() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    assert session.status().connection_state is EngineConnectionState.CONNECTED
    assert session.source_status() == ()
    assert session.midi_output_status() == ()
    assert session.device_catalogue(force_refresh=True) == DeviceCatalogue()
    assert session.node_memory_diagnostics() == ()
    assert session.node_profiles() == ()
    session.set_profiling_enabled(True)
    session.reset_profiling()
    assert session.wait_until_idle() is True
    assert client.calls == [
        "status",
        "source_status",
        "midi_output_status",
        "device_catalogue",
        "node_memory_diagnostics",
        "node_profiles",
        "set_profiling_enabled",
        "reset_profiling",
        "wait_until_idle",
    ]


def test_restart_outcome_rejects_inconsistent_values() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        RestartOutcome(True, "bogus")
    with pytest.raises(ValueError, match="must match"):
        RestartOutcome(True, "rejected")
