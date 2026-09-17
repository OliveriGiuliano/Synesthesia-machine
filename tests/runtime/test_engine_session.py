"""Headless EngineSession facade tests over a recording fake client.

The session depends only on the ``EngineClient`` protocol, so every test
drives it through a fake: no process, no Qt, no engine internals.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
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
    # Client-owned cursors, exposed so tests can assert their state.
    image_sequences: dict[tuple[UUID, str], int] = field(default_factory=dict)
    value_sequences: dict[tuple[UUID, str], int] = field(default_factory=dict)
    note_sequences: dict[UUID, int] = field(default_factory=dict)
    closed: bool = False

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        self.calls.append("activate")
        error = self.activate_error
        if error is not None:
            raise error
        self.activations.append((snapshot, None if demand_roots is None else tuple(demand_roots)))
        # The real clients reset their own preview state on every activation
        # outcome; the session no longer does it for them.
        self._reset_preview_state()
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

    def next_image_previews(self) -> tuple[ImagePreview, ...]:
        self.calls.append("next_image_previews")
        previews = tuple(
            preview
            for preview in self.image_previews
            if preview.sequence
            > self.image_sequences.get((preview.owner_id, preview.source_port_id), 0)
        )
        for preview in previews:
            self.image_sequences[(preview.owner_id, preview.source_port_id)] = preview.sequence
        return previews

    def next_note_previews(self) -> tuple[NotePreview, ...]:
        self.calls.append("next_note_previews")
        previews = tuple(
            preview
            for preview in self.note_previews
            if preview.sequence > self.note_sequences.get(preview.owner_id, 0)
        )
        for preview in previews:
            self.note_sequences[preview.owner_id] = preview.sequence
        return previews

    def next_value_previews(self) -> tuple[ValuePreview, ...]:
        self.calls.append("next_value_previews")
        previews = tuple(
            preview
            for preview in self.value_previews
            if preview.sequence
            > self.value_sequences.get((preview.owner_id, preview.source_port_id), 0)
        )
        for preview in previews:
            self.value_sequences[(preview.owner_id, preview.source_port_id)] = preview.sequence
        return previews

    def reset_image_preview_cursors(self) -> None:
        self.calls.append("reset_image_preview_cursors")
        self.image_sequences.clear()

    def reset_note_preview_cursors(self) -> None:
        self.calls.append("reset_note_preview_cursors")
        self.note_sequences.clear()

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
        if self.restart_result is not None:
            # A replaced engine generation resets the client's preview state.
            self._reset_preview_state()
        return self.restart_result

    def _reset_preview_state(self) -> None:
        self.image_sequences.clear()
        self.value_sequences.clear()
        self.note_sequences.clear()

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
    # The client owns the cursors: the session just asks for the next batch,
    # and each delivery advances the client's per-port cursor state.
    assert client.image_sequences == {
        (SOURCE_A, "output"): 2,
        (SOURCE_B, "output"): 1,
    }
    assert session.next_image_previews() == ()


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
    assert client.value_sequences == {
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
    assert client.note_sequences == {SOURCE_A: 2, SOURCE_B: 1}


def test_reset_image_preview_cursors_reserves_previews() -> None:
    client = _FakeEngineClient(image_previews=(_image_preview(SOURCE_A, "output", 1),))
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    assert session.next_image_previews() == ()
    session.reset_image_preview_cursors()
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
    result = session.restart()
    assert (result.ok, result.reason) == (False, "restart_failed")
    assert result.detail == "spawn failed"


def test_restart_reports_rebuild_failure() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client, demand_roots_for=lambda snapshot: (SOURCE_A,))
    session.activate(_snapshot())
    client.restart_result = None
    client.activate_error = RuntimeError("ipc broken")
    result = session.restart()
    assert (result.ok, result.reason) == (False, "rebuild_failed")
    assert result.detail == "ipc broken"


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
    # the client's cursors must not suppress the new frames.
    assert len(session.next_image_previews()) == 1


def test_failed_restart_keeps_preview_cursors() -> None:
    client = _FakeEngineClient(
        image_previews=(_image_preview(SOURCE_A, "output", 1),),
        restart_error=RuntimeError("spawn failed"),
    )
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    result = session.restart()
    assert (result.ok, result.reason) == (False, "restart_failed")
    assert result.detail == "spawn failed"
    # The old engine still owns its preview state: cursors stay advanced.
    assert session.next_image_previews() == ()


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


def test_poll_status_folds_machine_and_returns_status() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    state, status = session.poll_status()
    assert state is EngineConnectionState.CONNECTED
    assert status is not None
    assert status.connection_state is EngineConnectionState.CONNECTED
    # A stale heartbeat folds CONNECTED to UNRESPONSIVE; the status still arrives.
    client.last_heartbeat_offset_s = 5.0
    state, status = session.poll_status()
    assert state is EngineConnectionState.UNRESPONSIVE
    assert status is not None
    # An unreachable client folds to CRASHED without a status.
    client.status_error = RuntimeError("ipc down")
    state, status = session.poll_status()
    assert state is EngineConnectionState.CRASHED
    assert status is None


def test_poll_status_closed_session_polls_nothing() -> None:
    client = _FakeEngineClient()
    session = EngineSession(client)
    session.close()
    state, status = session.poll_status()
    assert state is EngineConnectionState.CLOSED
    assert status is None
    assert "status" not in client.calls


def test_per_family_cursor_resets_re_serve_only_their_family() -> None:
    client = _FakeEngineClient(
        image_previews=(_image_preview(SOURCE_A, "output", 1),),
        value_previews=(_value_preview(SOURCE_A, "output", 1),),
        note_previews=(_note_preview(SOURCE_A, 1),),
    )
    session = EngineSession(client)
    assert len(session.next_image_previews()) == 1
    assert len(session.next_value_previews()) == 1
    assert len(session.next_note_previews()) == 1
    assert session.next_image_previews() == ()
    assert session.next_value_previews() == ()
    assert session.next_note_previews() == ()

    # A family reset re-serves only that family's retained previews.
    session.reset_note_preview_cursors()
    assert session.next_image_previews() == ()
    assert session.next_value_previews() == ()
    assert len(session.next_note_previews()) == 1
    session.reset_image_preview_cursors()
    assert len(session.next_image_previews()) == 1
    assert session.next_note_previews() == ()
    assert session.next_value_previews() == ()
