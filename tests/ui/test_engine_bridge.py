"""Headless EngineBridge tests: orchestration behaviour with no Qt timers or windows.

The bridge is driven with a fake ``EngineClient`` (records calls, returns canned
values), a synchronous task runner (runs operations in place), and a scripted
clock (the tests fire the activation debounce deterministically). This proves
engine-orchestration behaviour — activation debouncing, the preview pump's
per-port cursors, transport dispatch, and status normalisation — is testable
without a ``QMainWindow`` or ``qWait`` timing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import numpy as np

from synesthesia_machine.contracts.engine_client import (
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
    SourceStatus,
    ValuePreview,
)
from synesthesia_machine.graph import ValidationReport
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.runtime import EngineSession
from synesthesia_machine.ui.engine_bridge import (
    EngineBridge,
    EngineBridgeState,
    PumpedPreviews,
)
from synesthesia_machine.ui.preview_router import PreviewRouter

_IMAGE_PORT = "image"
_OWNER_A = UUID("0" * 32)
_OWNER_B = UUID("1" * 32)


def _report() -> ValidationReport:
    return ValidationReport(issues=())


def _snapshot(revision: int = 1) -> GraphSnapshot:
    return GraphSnapshot(document_id=uuid4(), revision=revision, nodes=(), connections=())


class _FakeClient:
    """A full EngineClient fake: records calls, returns canned results."""

    def __init__(self) -> None:
        self.activations: list[tuple[object, tuple[UUID, ...] | None]] = []
        self.transport: list[tuple[str, UUID | None]] = []
        self.panics = 0
        self.image_polls: list[Mapping[tuple[UUID, str], int] | None] = []
        self.value_polls: list[Mapping[tuple[UUID, str], int] | None] = []
        self.note_polls: list[Mapping[UUID, int] | None] = []
        self.status_cycle: list[EngineConnectionState] = []
        self.device_requests: list[bool] = []
        self.cleared_previews = 0
        self.close_count = 0
        self.restart_count = 0
        self.image_preview: ImagePreview | None = None
        self.value_previews: tuple[ValuePreview, ...] = ()

    # -- transport ---------------------------------------------------------

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        roots = tuple(demand_roots) if demand_roots is not None else None
        self.activations.append((snapshot, roots))
        return EngineActivation(graph_revision=7, report=_report(), activated=True)

    def play(self, source_node_id: UUID | None = None) -> None:
        self.transport.append(("play", source_node_id))

    def pause(self, source_node_id: UUID | None = None) -> None:
        self.transport.append(("pause", source_node_id))

    def resume(self, source_node_id: UUID | None = None) -> None:
        self.transport.append(("resume", source_node_id))

    def stop(self, source_node_id: UUID | None = None) -> None:
        self.transport.append(("stop", source_node_id))

    def reload(self, source_node_id: UUID | None = None) -> None:
        self.transport.append(("reload", source_node_id))

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        self.transport.append(("seek", source_node_id))

    def panic(self) -> None:
        self.panics += 1

    def restart(self) -> EngineActivation | None:
        self.restart_count += 1
        return None

    # -- status ------------------------------------------------------------

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        return ()

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        return ()

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        return ()

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        self.device_requests.append(force_refresh)
        return DeviceCatalogue(devices=())

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        return ()

    def set_profiling_enabled(self, enabled: bool) -> None:
        del enabled

    def reset_profiling(self) -> None:
        pass

    def metrics(self) -> EngineMetrics:
        return EngineMetrics(state=EngineState.RUNNING)

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        self.image_polls.append(after_sequences)
        if self.image_preview is None:
            return ()
        key = (self.image_preview.owner_id, self.image_preview.source_port_id)
        cursor = after_sequences.get(key, 0) if after_sequences is not None else 0
        if self.image_preview.sequence <= cursor:
            return ()
        return (self.image_preview,)

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        self.value_polls.append(after_sequences)
        result: list[ValuePreview] = []
        for preview in self.value_previews:
            key = (preview.owner_id, preview.source_port_id)
            cursor = after_sequences.get(key, 0) if after_sequences is not None else 0
            if preview.sequence > cursor:
                result.append(preview)
        return tuple(result)

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        self.note_polls.append(after_sequences)
        return ()

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def status(self) -> EngineStatus:
        if not self.status_cycle:
            return EngineStatus(connection_state=EngineConnectionState.CONNECTED)
        return EngineStatus(connection_state=self.status_cycle.pop(0))

    def clear_previews(self) -> None:
        self.cleared_previews += 1

    def close(self) -> None:
        self.close_count += 1


@dataclass
class _SyncRunner:
    """Runs operations in place, delivering results or errors synchronously."""

    def submit(
        self,
        kind: str,
        operation: Callable[[], object],
        *,
        on_result: Callable[[str, object], None],
        on_error: Callable[[str, object], None],
    ) -> bool:
        try:
            on_result(kind, operation())
        except Exception as error:
            on_error(kind, error)
        return True


class _ScriptedClock:
    """Records one-shot callbacks; tests fire them explicitly (the debounce)."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, Callable[[], None]]] = []

    def schedule_once(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.scheduled.append((delay_ms, callback))

    def cancel(self, callback: Callable[[], None]) -> None:
        self.scheduled = [(d, c) for (d, c) in self.scheduled if c is not callback]

    def fire_all(self) -> None:
        due = [callback for (_delay, callback) in self.scheduled]
        self.scheduled.clear()
        for callback in due:
            callback()


@dataclass
class _Harness:
    bridge: EngineBridge
    client: _FakeClient
    runner: _SyncRunner
    clock: _ScriptedClock
    states: list[EngineBridgeState]
    previews: list[PumpedPreviews]
    messages: list[tuple[str, int]]


def _harness(
    client: _FakeClient,
    *,
    demand_roots: tuple[UUID, ...] = (),
) -> _Harness:
    states: list[EngineBridgeState] = []
    previews: list[PumpedPreviews] = []
    messages: list[tuple[str, int]] = []
    runner = _SyncRunner()
    clock = _ScriptedClock()
    session = EngineSession(client)
    bridge = EngineBridge(
        session,
        demand_roots_for=lambda _snapshot: demand_roots,
        task_runner=runner,
        clock=clock,
        preview_router=PreviewRouter(),
        on_previews=previews.append,
        on_state=states.append,
        on_status_message=lambda text, ms: messages.append((text, ms)),
    )
    return _Harness(bridge, client, runner, clock, states, previews, messages)


def _image_preview() -> ImagePreview:
    data = np.zeros((2, 2, 4), dtype=np.uint8)
    data.flags.writeable = False
    return ImagePreview(
        owner_id=_OWNER_A,
        source_port_id=_IMAGE_PORT,
        sequence=1,
        tick_index=1,
        width=2,
        height=2,
        channels=4,
        data=data,
    )


def _value_preview(text: str) -> ValuePreview:
    return ValuePreview(
        owner_id=_OWNER_B,
        source_port_id="value",
        sequence=1,
        tick_index=1,
        port_type="FLOAT",
        text=text,
    )


# --- activation ------------------------------------------------------------


def test_activation_is_debounced_into_a_single_call() -> None:
    client = _FakeClient()
    harness = _harness(client, demand_roots=(uuid4(),))
    harness.bridge.schedule_activation(_snapshot())
    harness.bridge.schedule_activation(_snapshot())
    harness.bridge.schedule_activation(_snapshot())
    assert client.activations == [], "no activation until the debounce fires"
    harness.clock.fire_all()
    assert len(client.activations) == 1


def test_debounce_collapses_to_latest_snapshot() -> None:
    client = _FakeClient()
    harness = _harness(client)
    first, second = _snapshot(1), _snapshot(2)
    harness.bridge.schedule_activation(first)
    harness.bridge.schedule_activation(second)
    harness.clock.fire_all()
    assert [snapshot for snapshot, _ in client.activations] == [second]


def test_rejected_activation_clears_previews_and_reports() -> None:
    client = _FakeClient()
    client.activate = lambda snapshot, *, demand_roots=None: EngineActivation(  # type: ignore[method-assign]
        graph_revision=1, report=_report(), activated=False
    )
    harness = _harness(client)
    harness.bridge.schedule_activation(_snapshot())
    harness.clock.fire_all()
    assert client.cleared_previews == 1
    assert ("activation_rejected", 5000) in harness.messages


# --- preview pump ----------------------------------------------------------


def test_preview_pump_routes_batch_and_advances_cursors() -> None:
    client = _FakeClient()
    client.image_preview = _image_preview()
    client.value_previews = (_value_preview("1.00"),)
    harness = _harness(client)
    harness.bridge.pump_previews_once(note_visible=False)
    assert len(harness.previews) == 1
    batch = harness.previews[0]
    assert len(batch.image_previews) == 1
    assert len(batch.value_previews) == 1
    # The cursor advanced to the emitted sequence, so a repeat poll emits nothing new.
    harness.bridge.pump_previews_once(note_visible=False)
    assert harness.previews[-1].image_previews == ()


def test_hidden_note_dock_skips_note_polling() -> None:
    client = _FakeClient()
    harness = _harness(client)
    harness.bridge.pump_previews_once(note_visible=False)
    assert client.note_polls == [], "note port must not be polled while the dock is hidden"


def test_hiding_the_note_dock_resets_its_cursors() -> None:
    client = _FakeClient()
    harness = _harness(client)
    harness.bridge.pump_previews_once(note_visible=True)
    harness.bridge.note_preview_hidden()
    assert client.note_polls, "the dock was visible, so the note port was polled once"


# --- transport -------------------------------------------------------------


def test_transport_commands_dispatch() -> None:
    client = _FakeClient()
    harness = _harness(client)
    target = uuid4()
    harness.bridge.play(target)
    harness.bridge.pause(target)
    harness.bridge.stop_source(target)
    harness.bridge.seek(target, 1.5)
    harness.bridge.panic()
    assert client.transport == [
        ("play", target),
        ("pause", target),
        ("stop", target),
        ("seek", target),
    ]
    assert client.panics == 1


# --- status / telemetry ----------------------------------------------------


def test_refresh_status_publishes_connection_state() -> None:
    client = _FakeClient()
    client.status_cycle = [EngineConnectionState.CRASHED]
    harness = _harness(client)
    harness.bridge.refresh_status()
    assert harness.states[-1].connection_state is EngineConnectionState.CRASHED


def test_device_catalogue_round_trips_into_state() -> None:
    client = _FakeClient()
    harness = _harness(client)
    harness.bridge.request_device_catalogue(force_refresh=True)
    assert client.device_requests == [True]
    assert harness.states[-1].device_catalogue is not None
    assert harness.states[-1].device_catalogue.devices == ()


# --- lifecycle -------------------------------------------------------------


def test_close_cancels_pending_activation_and_closes_client() -> None:
    client = _FakeClient()
    harness = _harness(client)
    harness.bridge.schedule_activation(_snapshot())
    harness.bridge.close()
    harness.clock.fire_all()
    assert client.activations == []
    assert client.close_count == 1


def test_closed_bridge_ignores_new_intents() -> None:
    client = _FakeClient()
    harness = _harness(client)
    harness.bridge.close()
    target = uuid4()
    harness.bridge.play(target)
    harness.bridge.schedule_activation(_snapshot())
    harness.clock.fire_all()
    assert client.transport == []
    assert client.activations == []
