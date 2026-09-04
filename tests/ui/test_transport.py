"""EngineClient-only editor activation, transport, status, preview, and close tests."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontMetricsF, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QToolButton

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    MidiOutputConnectionState,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NodeProfile,
    NoteActivity,
    NotePreview,
    SourceState,
    SourceStatus,
    ValuePreview,
    freeze_uint8_preview,
)
from synesthesia_machine.graph import GraphSnapshot, ValidationReport
from synesthesia_machine.ui.main_window import MainWindow
from synesthesia_machine.ui.previews import NotePreviewWidget, note_rainbow_color

SOURCE_A = UUID("00000000-0000-0000-0000-000000000701")
SOURCE_B = UUID("00000000-0000-0000-0000-000000000702")
NOTE_NODE = UUID("00000000-0000-0000-0000-000000000704")
MIDI_OUTPUT_NODE = UUID("00000000-0000-0000-0000-000000000705")


def _snapshot_list() -> list[GraphSnapshot]:
    return []


def _call_list() -> list[tuple[str, UUID | None]]:
    return []


def _status_map() -> dict[UUID, SourceStatus]:
    return {}


@dataclass(slots=True)
class _RecordingEngineClient:
    activations: list[GraphSnapshot] = field(default_factory=_snapshot_list)
    calls: list[tuple[str, UUID | None]] = field(default_factory=_call_list)
    statuses: dict[UUID, SourceStatus] = field(default_factory=_status_map)
    image_previews: tuple[ImagePreview, ...] = ()
    note_previews: tuple[NotePreview, ...] = ()
    value_previews: tuple[ValuePreview, ...] = ()
    memory_diagnostics: tuple[NodeMemoryDiagnostic, ...] = ()
    midi_statuses: tuple[MidiOutputStatus, ...] = ()
    closed: bool = False
    activation_started: threading.Event | None = None
    activation_release: threading.Event | None = None
    metrics_started: threading.Event | None = None
    metrics_release: threading.Event | None = None

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        del demand_roots
        if self.activation_started is not None:
            self.activation_started.set()
        if self.activation_release is not None:
            assert self.activation_release.wait(2.0)
        self.activations.append(snapshot)
        return EngineActivation(snapshot.revision, ValidationReport(), True)

    def play(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("play", source_node_id))

    def pause(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("pause", source_node_id))

    def resume(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("resume", source_node_id))

    def stop(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("stop", source_node_id))

    def reload(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("reload", source_node_id))

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        del source_time_s
        self.calls.append(("seek", source_node_id))

    def panic(self) -> None:
        self.calls.append(("panic", None))

    def clear_previews(self) -> None:
        self.calls.append(("clear_previews", None))

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        if source_node_id is not None:
            status = self.statuses.get(source_node_id)
            return () if status is None else (status,)
        return tuple(self.statuses[key] for key in sorted(self.statuses, key=str))

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        if output_node_id is None:
            return self.midi_statuses
        return tuple(status for status in self.midi_statuses if status.node_id == output_node_id)

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        del force_refresh
        return DeviceCatalogue()

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        if node_id is None:
            return self.memory_diagnostics
        return tuple(
            diagnostic for diagnostic in self.memory_diagnostics if diagnostic.node_id == node_id
        )

    def metrics(self) -> EngineMetrics:
        if self.metrics_started is not None:
            self.metrics_started.set()
        if self.metrics_release is not None:
            assert self.metrics_release.wait(2.0)
        return EngineMetrics(
            EngineState.RUNNING,
            graph_revision=4,
            processed_ticks=42,
            processed_fps=29.5,
            p95_node_time_ms=2.25,
            dropped_before_processing=3,
            memory_bytes=64 * 1024 * 1024,
        )

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        return ()

    def set_profiling_enabled(self, enabled: bool) -> None:
        del enabled

    def reset_profiling(self) -> None:
        return

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.image_previews
            if preview.sequence > thresholds.get((preview.owner_id, preview.source_port_id), 0)
        )

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.note_previews
            if preview.sequence > thresholds.get(preview.owner_id, 0)
        )

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.value_previews
            if preview.sequence > thresholds.get((preview.owner_id, preview.source_port_id), 0)
        )

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def status(self) -> EngineStatus:
        return EngineStatus(EngineConnectionState.CONNECTED, graph_revision=4)

    def restart(self) -> EngineActivation | None:
        snapshot = self.activations[-1] if self.activations else None
        if snapshot is None:
            return None
        return EngineActivation(snapshot.revision, ValidationReport(), True)

    def close(self) -> None:
        self.closed = True


def _paths(root: Path) -> ApplicationPaths:
    data = root / "data"
    return ApplicationPaths(data, data / "logs", data / "recovery")


@pytest.fixture
def runtime_window(
    qapp: QApplication, tmp_path: Path
) -> Iterator[tuple[MainWindow, _RecordingEngineClient]]:
    registry = create_application_registry()
    client = _RecordingEngineClient()
    window = MainWindow(
        registry,
        _paths(tmp_path),
        client,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    window.show()
    qapp.processEvents()
    yield window, client
    if not client.closed:
        window.session.new_document()
        window.close()


def _source_status(node_id: UUID, state: SourceState = SourceState.READY) -> SourceStatus:
    return SourceStatus(node_id, state, f"{node_id}.mp4")


def test_session_changes_debounce_graph_activation_through_client(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    assert window._activation_timer.isActive()
    assert client.activations == []

    QTest.qWait(140)

    assert len(client.activations) == 1
    assert len(client.activations[0].nodes) == 1


def test_activation_and_metrics_ipc_never_block_qt_event_thread(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, client = runtime_window
    window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    client.activation_started = threading.Event()
    client.activation_release = threading.Event()

    started = time.perf_counter()
    window._activate_graph()
    assert time.perf_counter() - started < 0.1
    assert client.activation_started.wait(1.0)
    assert window.isVisible()
    client.activation_release.set()
    QTest.qWait(20)

    client.metrics_started = threading.Event()
    client.metrics_release = threading.Event()
    started = time.perf_counter()
    window._refresh_engine_status()
    assert time.perf_counter() - started < 0.1
    assert client.metrics_started.wait(1.0)
    qapp.processEvents()
    assert window.isVisible()
    client.metrics_release.set()
    QTest.qWait(20)


def test_transport_auto_targets_sole_source_resumes_paused_and_never_fans_out(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    source_a = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    client.statuses[source_a] = _source_status(source_a)
    window.play()
    assert client.calls[-1] == ("play", source_a)

    client.statuses[source_a] = _source_status(source_a, SourceState.PAUSED)
    window.play()
    assert client.calls[-1] == ("resume", source_a)

    source_b = window.session.add_node("synmachine.input.load_video", (300.0, 0.0))
    client.statuses[source_b] = _source_status(source_b)
    count = len(client.calls)
    window.stop()
    assert len(client.calls) == count
    assert "Select one source" in window.statusBar().currentMessage()

    window.scene.select_node_ids({source_b})
    window.reload()
    assert client.calls[-1] == ("reload", source_b)

    window.scene.select_node_ids({source_a, source_b})
    count = len(client.calls)
    window.pause()
    assert len(client.calls) == count


def test_transport_commands_invalidate_known_stopped_cache(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    # Transport commands change engine run state outside of an activation;
    # without invalidating the cache, a stopped status read before the
    # command would keep the UI showing a stale STOPPED engine and skip
    # the periodic refresh until the next activation.
    window, client = runtime_window
    source_a = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    client.statuses[source_a] = _source_status(source_a)

    window._engine_known_stopped = True
    window.play()
    assert client.calls[-1] == ("play", source_a)
    assert window._engine_known_stopped is False

    window._engine_known_stopped = True
    client.statuses[source_a] = _source_status(source_a, SourceState.PAUSED)
    window.play()
    assert client.calls[-1] == ("resume", source_a)
    assert window._engine_known_stopped is False

    window._engine_known_stopped = True
    window.stop()
    assert client.calls[-1] == ("stop", source_a)
    assert window._engine_known_stopped is False


def test_successful_restart_invalidate_known_stopped_cache(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    # A pre-crash metrics refresh can hold the "known stopped" cache True
    # when the user restarts the engine; the restarted engine may
    # auto-activate and run immediately, so the cache must be dropped at
    # restart instead of at the next activation.
    window, client = runtime_window
    del client
    window.session.add_node("synmachine.utility.number", (0.0, 0.0))

    window._engine_known_stopped = True
    window.restart_engine()
    assert window._engine_known_stopped is False


def test_transport_toolbar_has_named_visible_hover_press_controls_and_click_feedback(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    client.statuses[source_id] = _source_status(source_id)
    window._refresh_action_states()

    for key in ("play", "pause", "stop", "reload"):
        action = window.action_registry.require(key)
        button = window.transport_toolbar.widgetForAction(action)
        assert isinstance(button, QToolButton)
        assert button.objectName() == f"transport_{key}_button"
        assert button.property("transportControl") is True
        assert button.accessibleName() == action.text().replace("&", "")
        assert button.isEnabled()
        assert str(source_id)[:8] not in action.toolTip()

    style_sheet = window.styleSheet()
    assert 'QToolButton[transportControl="true"]:hover' in style_sheet
    assert 'QToolButton[transportControl="true"]:pressed' in style_sheet
    assert 'QToolButton[transportControl="true"]:disabled' in style_sheet

    play_button = window.transport_toolbar.widgetForAction(window.action_registry.require("play"))
    assert isinstance(play_button, QToolButton)
    QTest.mouseClick(play_button, Qt.MouseButton.LeftButton)
    assert client.calls[-1] == ("play", source_id)
    assert "Playing source" in window.statusBar().currentMessage()


def test_preview_and_metrics_polling_update_ui_with_sequence_coalescing(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    display_id = window.session.add_node(
        "synmachine.visualization.display_image_data", (300.0, 0.0)
    )
    window.session.add_connection(source_id, "image", display_id, "image")
    data = freeze_uint8_preview(np.full((2, 3, 3), 127, dtype=np.uint8))
    image = ImagePreview(source_id, "image", 1, 7, 3, 2, 3, data)
    notes = NotePreview(NOTE_NODE, 1, 7, (NoteActivity(0, 60, 100),))
    client.image_previews = (image,)
    client.note_previews = (notes,)

    window._poll_previews()
    window._refresh_engine_status()

    assert window.image_preview_panel.image_widget.latest_preview is image
    assert window.note_preview_panel.note_widget.latest_preview is notes
    assert window.image_preview_dock is not window.note_preview_dock
    assert window.image_preview_panel.image_widget.isVisible()
    assert window.note_preview_panel.note_widget.isVisible()
    assert "sequence 1" in window.image_preview_panel.image_caption.text()
    assert window._image_sequences == {(source_id, "image"): 1}
    assert window._note_sequences == {NOTE_NODE: 1}
    assert "42 ticks" in window._engine_status.text()
    assert "3 dropped" in window._engine_status.text()
    assert "Input / processed / preview: 0.0 / 29.5 / 0.0 FPS" in (window._engine_status.toolTip())


def test_image_dock_only_uses_the_source_feeding_an_image_visualizer(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, client = runtime_window
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    resize_id = window.session.add_node("synmachine.image.resize", (300.0, 0.0))
    display_id = window.session.add_node(
        "synmachine.visualization.display_image_data", (600.0, 0.0)
    )
    source_connection = window.session.add_connection(source_id, "image", resize_id, "image")
    display_connection = window.session.add_connection(resize_id, "image", display_id, "image")
    qapp.processEvents()
    data = freeze_uint8_preview(np.full((2, 3, 3), 127, dtype=np.uint8))
    display_preview = ImagePreview(resize_id, "image", 1, 7, 3, 2, 3, data)
    unrelated_preview = ImagePreview(source_id, "image", 1, 7, 3, 2, 3, data)
    client.image_previews = (display_preview, unrelated_preview)

    window._poll_previews()

    assert window.image_preview_panel.image_widget.latest_preview is display_preview
    assert window.scene.connection_items[source_connection]._image is not None
    assert window.scene.connection_items[display_connection]._image is not None


def test_successful_activation_clears_all_stale_runtime_previews(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, _client = runtime_window
    number_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    math_id = window.session.add_node("synmachine.utility.math", (300.0, 0.0))
    value_connection = window.session.add_connection(number_id, "value", math_id, "a")
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 200.0))
    display_id = window.session.add_node(
        "synmachine.visualization.display_image_data", (300.0, 200.0)
    )
    image_connection = window.session.add_connection(source_id, "image", display_id, "image")
    qapp.processEvents()
    thumbnail = QImage(8, 8, QImage.Format.Format_RGB32)
    data = freeze_uint8_preview(np.full((2, 3, 3), 127, dtype=np.uint8))
    image_preview = ImagePreview(source_id, "image", 1, 7, 3, 2, 3, data)
    note_preview = NotePreview(NOTE_NODE, 1, 7, (NoteActivity(0, 60, 100),))
    window.scene.set_connection_value_preview(number_id, "value", "42")
    window.scene.set_connection_image_preview(source_id, "image", thumbnail)
    window.image_preview_panel.show_preview(image_preview)
    window.note_preview_panel.show_preview(note_preview)
    window._image_sequences[(source_id, "image")] = 1
    window._note_sequences[NOTE_NODE] = 1
    window._canvas_value_sequences[(number_id, "value")] = 1

    window._apply_engine_activation(
        EngineActivation(window.session.document.revision, ValidationReport(), True)
    )

    assert window.scene.connection_items[value_connection]._value_text is None
    assert window.scene.connection_items[image_connection]._image is None
    assert window.image_preview_panel.image_widget.latest_preview is None
    assert window.note_preview_panel.note_widget.latest_preview is None
    assert window._image_sequences == {}
    assert window._note_sequences == {}
    assert window._canvas_value_sequences == {}


def test_node_drag_release_preserves_live_connection_pills(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, client = runtime_window
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    resize_id = window.session.add_node("synmachine.image.resize", (300.0, 0.0))
    display_id = window.session.add_node(
        "synmachine.visualization.display_image_data", (600.0, 0.0)
    )
    source_connection = window.session.add_connection(source_id, "image", resize_id, "image")
    display_connection = window.session.add_connection(resize_id, "image", display_id, "image")
    QTest.qWait(160)
    qapp.processEvents()
    client.activations.clear()

    source_thumbnail = QImage(8, 8, QImage.Format.Format_RGB32)
    resized_thumbnail = QImage(12, 8, QImage.Format.Format_RGB32)
    window.scene.set_connection_image_preview(source_id, "image", source_thumbnail)
    window.scene.set_connection_image_preview(resize_id, "image", resized_thumbnail)
    source_item = window.scene.connection_items[source_connection]
    display_item = window.scene.connection_items[display_connection]
    old_position = window.session.document.node(resize_id)
    assert old_position is not None

    window.session.move_nodes({resize_id: old_position.position}, {resize_id: (360.0, 40.0)})
    qapp.processEvents()

    assert not window._activation_timer.isActive()
    assert window.scene.connection_items[source_connection] is source_item
    assert window.scene.connection_items[display_connection] is display_item
    assert source_item._image is source_thumbnail
    assert display_item._image is resized_thumbnail
    QTest.qWait(140)
    assert client.activations == []
    assert source_item._image is source_thumbnail
    assert display_item._image is resized_thumbnail


def test_image_and_note_visualizers_have_independent_dock_tabs_and_demand_roots(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, _client = runtime_window
    image_id = window.session.add_node("synmachine.visualization.display_image_data", (0.0, 0.0))
    note_id = window.session.add_node("synmachine.visualization.note_visualizer", (300.0, 0.0))
    snapshot = window.session.document.snapshot()

    assert window.image_preview_dock.isVisible()
    assert window.note_preview_dock.isVisible()
    assert set(window._runtime_demand_roots(snapshot)) == {image_id, note_id}

    window.note_preview_dock.hide()
    qapp.processEvents()
    assert set(window._runtime_demand_roots(snapshot)) == {image_id}
    assert window.image_preview_dock.isVisible()

    window.note_preview_dock.show()
    window.image_preview_dock.hide()
    qapp.processEvents()
    assert set(window._runtime_demand_roots(snapshot)) == {note_id}
    assert window.note_preview_dock.isVisible()


def test_image_link_pill_keeps_producer_demanded_when_display_dock_hidden(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, _client = runtime_window
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    display_id = window.session.add_node(
        "synmachine.visualization.display_image_data", (300.0, 0.0)
    )
    connection_id = window.session.add_connection(source_id, "image", display_id, "image")
    snapshot = window.session.document.snapshot()
    assert any(connection.destination_node_id == display_id for connection in snapshot.connections)

    # Dock visible: the display node is a demand root regardless of the pill.
    window.image_preview_dock.show()
    qapp.processEvents()
    assert display_id in window._runtime_demand_roots(snapshot)

    # Dock hidden but the pill is visible (the default): the display node is no
    # longer a root, but the pill keeps its producer (the source) demanded.
    window.image_preview_dock.hide()
    qapp.processEvents()
    roots = window._runtime_demand_roots(snapshot)
    assert display_id not in roots
    assert source_id in roots

    # Hiding the link pill drops the producer root, so nothing in the chain is demanded.
    window.session.set_connection_preview_visible(connection_id, False)
    snapshot = window.session.document.snapshot()
    roots = window._runtime_demand_roots(snapshot)
    assert display_id not in roots
    assert source_id not in roots

    # Showing the dock demands the display node again even with the pill hidden.
    window.image_preview_dock.show()
    qapp.processEvents()
    assert display_id in window._runtime_demand_roots(snapshot)


def test_scalar_link_pill_keeps_producer_demanded_without_a_display_root(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, _client = runtime_window
    number_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    math_id = window.session.add_node("synmachine.utility.math", (300.0, 0.0))
    connection_id = window.session.add_connection(number_id, "value", math_id, "a")
    qapp.processEvents()
    snapshot = window.session.document.snapshot()

    # Both nodes are stateless (no display/sink root), so the scalar producer is
    # demanded only because its link pill is visible (the default).
    roots = window._runtime_demand_roots(snapshot)
    assert number_id in roots
    assert math_id not in roots

    # Hiding the link pill drops the scalar producer root; nothing in the chain is demanded.
    window.session.set_connection_preview_visible(connection_id, False)
    snapshot = window.session.document.snapshot()
    roots = window._runtime_demand_roots(snapshot)
    assert number_id not in roots
    assert math_id not in roots


def test_live_previews_route_to_the_correct_link_pills(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    window, _client = runtime_window
    number_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    math_id = window.session.add_node("synmachine.utility.math", (300.0, 0.0))
    value_conn = window.session.add_connection(number_id, "value", math_id, "a")
    video_id = window.session.add_node("synmachine.input.load_video", (0.0, 200.0))
    display_id = window.session.add_node(
        "synmachine.visualization.display_image_data", (300.0, 200.0)
    )
    image_conn = window.session.add_connection(video_id, "image", display_id, "image")
    qapp.processEvents()

    value_item = window.scene.connection_items[value_conn]
    image_item = window.scene.connection_items[image_conn]
    assert value_item.takes_value_pill()
    assert image_item.takes_image_pill()

    # The scalar value is owned by its producer port and only touches that pill.
    window.scene.set_connection_value_preview(number_id, "value", "42")
    assert value_item._value_text == "42"
    assert image_item._value_text is None

    # A preview aimed at a different port on the same producer must not bleed over.
    window.scene.set_connection_value_preview(number_id, "unconnected_port", "99")
    assert value_item._value_text == "42"

    # The image is owned by its producer port and only touches the matching pill.
    thumbnail = QImage(8, 8, QImage.Format.Format_RGB32)
    window.scene.set_connection_image_preview(video_id, "image", thumbnail)
    assert image_item._image is thumbnail
    assert value_item._image is None


def test_image_link_pill_routed_per_port_for_multi_output_producer(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    """A producer with several connected image/channel outputs (e.g. a channel
    separator) shows each output's own frame on its own pill. A preview aimed at
    one port must not bleed onto a sibling port's pill.
    """

    window, _client = runtime_window
    video_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    separate_id = window.session.add_node("synmachine.image.separate_channels", (300.0, 0.0))
    display_a = window.session.add_node("synmachine.visualization.channel_display", (0.0, 200.0))
    # A non-visualizer channel consumer so the two outputs reach distinct destinations
    # (image visualizers are singletons and would replace one another).
    pitch_id = window.session.add_node("synmachine.synesthesia.channel_to_pitch", (300.0, 200.0))
    window.session.add_connection(video_id, "image", separate_id, "image")
    conn_a = window.session.add_connection(separate_id, "channel_1", display_a, "channel")
    conn_b = window.session.add_connection(separate_id, "channel_2", pitch_id, "value")
    qapp.processEvents()

    item_a = window.scene.connection_items[conn_a]
    item_b = window.scene.connection_items[conn_b]
    assert item_a.takes_image_pill() and item_b.takes_image_pill()

    thumb_a = QImage(8, 8, QImage.Format.Format_RGB32)
    thumb_b = QImage(8, 8, QImage.Format.Format_RGB32)
    # channel_1's frame lands only on channel_1's pill, leaving channel_2 neutral.
    window.scene.set_connection_image_preview(separate_id, "channel_1", thumb_a)
    assert item_a._image is thumb_a
    assert item_b._image is None
    # channel_2's frame lands only on channel_2's pill, keeping channel_1 intact.
    window.scene.set_connection_image_preview(separate_id, "channel_2", thumb_b)
    assert item_b._image is thumb_b
    assert item_a._image is thumb_a
    # A preview for a port with no pill (e.g. channel_3) touches neither.
    window.scene.set_connection_image_preview(separate_id, "channel_3", thumb_b)
    assert item_a._image is thumb_a
    assert item_b._image is thumb_b


def test_scalar_link_pill_content_rect_fits_the_font(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
    qapp: QApplication,
) -> None:
    """The scalar pill's text rect must be a full font line tall.

    Regression: a fixed 22px body with 8px vertical padding left only a 6px
    window for the number, slicing the top and bottom of the digits.
    """
    window, _client = runtime_window
    number_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    math_id = window.session.add_node("synmachine.utility.math", (300.0, 0.0))
    connection_id = window.session.add_connection(number_id, "value", math_id, "a")
    qapp.processEvents()

    item = window.scene.connection_items[connection_id]
    assert item.takes_value_pill()
    item.set_value_preview("0.5")

    body = item._pill_body_rect()
    assert body is not None
    content = item._pill_content_rect(body)
    font_height = QFontMetricsF(item.theme.body_font()).height()
    assert content.height() >= font_height


def test_note_velocity_chart_uses_rainbow_bars_and_adapts_to_aspect_ratio(
    qapp: QApplication,
) -> None:
    widget = NotePreviewWidget()
    widget.resize(560, 180)
    widget.set_preview(
        NotePreview(
            NOTE_NODE,
            1,
            7,
            (NoteActivity(0, 36, 28), NoteActivity(0, 84, 118)),
        )
    )
    widget.show()
    qapp.processEvents()

    assert widget.note_axis_orientation() is Qt.Orientation.Horizontal
    colors = {note_rainbow_color(note).name() for note in range(128)}
    assert len(colors) == 128
    image = widget.grab().toImage()
    low_color = note_rainbow_color(36).rgb()
    high_color = note_rainbow_color(84).rgb()
    low_pixels = sum(
        image.pixel(x, y) == low_color for x in range(image.width()) for y in range(image.height())
    )
    high_pixels = sum(
        image.pixel(x, y) == high_color for x in range(image.width()) for y in range(image.height())
    )
    assert high_pixels > low_pixels > 0

    widget.resize(180, 560)
    qapp.processEvents()
    assert widget.note_axis_orientation() is Qt.Orientation.Vertical
    widget.close()


def test_source_error_detail_is_visible_in_status_bar_and_engine_tooltip(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    source_id = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    error = "Video file does not exist: C:\\missing\\clip.mkv"
    client.statuses[source_id] = SourceStatus(
        source_id,
        SourceState.ERROR,
        "C:\\missing\\clip.mkv",
        last_error=error,
    )

    window._refresh_engine_status()

    expected = f"Source {str(source_id)[:8]}: {error}"
    assert window.statusBar().currentMessage() == expected
    assert window._engine_status.toolTip() == expected


def test_midi_output_error_lists_friendly_available_ports_in_runtime_feedback(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    error = "MIDI output 'Missing port' is not currently available"
    client.midi_statuses = (
        MidiOutputStatus(
            MIDI_OUTPUT_NODE,
            MidiOutputConnectionState.UNAVAILABLE,
            "Missing port",
            ("loopMIDI Port", "Microsoft GS Wavetable Synth"),
            last_error=error,
        ),
    )

    window._refresh_engine_status()

    assert "MIDI UNAVAILABLE" in window._engine_status.text()
    assert error in window._engine_status.toolTip()
    assert "loopMIDI Port" in window._engine_status.toolTip()
    assert error in window.statusBar().currentMessage()


def test_inspector_choice_commit_waits_for_popup_signal_before_rebuilding_editor(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, _client = runtime_window
    audio_id = window.session.add_node("synmachine.output.generate_audio", (0.0, 0.0))
    window.scene.select_node_ids({audio_id})
    combo = window.inspector.findChild(QComboBox, "parameter_waveform")
    assert combo is not None

    square_index = combo.findData("SQUARE")
    assert square_index >= 0
    combo.setCurrentIndex(square_index)

    assert window.inspector.findChild(QComboBox, "parameter_waveform") is combo
    node = window.session.document.node(audio_id)
    assert node is not None and node.parameters["waveform"] == "SQUARE"
    assert window.inspector._refresh_timer.isActive()

    QTest.qWait(1)

    node = window.session.document.node(audio_id)
    assert node is not None and node.parameters["waveform"] == "SQUARE"
    replacement = window.inspector.findChild(QComboBox, "parameter_waveform")
    assert replacement is not None and replacement.currentData() == "SQUARE"


def test_inspector_and_combo_popup_have_explicit_dark_theme_surfaces(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, _client = runtime_window
    audio_id = window.session.add_node("synmachine.output.generate_audio", (0.0, 0.0))
    window.scene.select_node_ids({audio_id})

    assert window.inspector.objectName() == "inspector_panel"
    assert window.inspector.scroll_area.objectName() == "inspector_scroll_area"
    assert window.inspector.scroll_area.viewport().objectName() == "inspector_scroll_viewport"
    assert window.inspector.form_container.objectName() == "inspector_form_container"
    style_sheet = window.styleSheet()
    assert "QWidget#inspector_form_container" in style_sheet
    assert "QComboBox QAbstractItemView" in style_sheet
    assert "QComboBox QAbstractItemView::item:selected" in style_sheet
    application = QApplication.instance()
    assert application is not None
    assert "QAbstractItemView#parameter_choice_popup" in application.styleSheet()

    inline_proxy = window.scene.node_items[audio_id].parameter_editors["waveform"]
    inline_combo = inline_proxy.widget()
    assert isinstance(inline_combo, QComboBox)
    original_z = inline_proxy.zValue()
    inline_combo.showPopup()
    assert inline_proxy.zValue() == 10_000.0
    assert inline_combo.view().objectName() == "parameter_choice_popup"
    inline_combo.hidePopup()
    assert inline_proxy.zValue() == original_z


def test_selected_node_memory_diagnostic_is_published_to_inspector(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    hold_id = window.session.add_node("synmachine.image.hold_image", (0.0, 0.0))
    client.memory_diagnostics = (
        NodeMemoryDiagnostic(
            hold_id,
            estimated_retained_bytes=2 * 1024 * 1024,
            retained_bytes=1024 * 1024,
            retained_frame_count=1,
            capacity_frame_count=2,
            memory_limit_bytes=256 * 1024 * 1024,
        ),
    )
    window.scene.select_node_ids({hold_id})

    window._refresh_engine_status()

    labels = {label.text() for label in window.inspector.findChildren(QLabel)}
    assert "2.00 MiB" in labels
    assert "1.00 MiB (1/2 frames)" in labels
    assert "256.00 MiB" in labels


def test_panic_and_accepted_close_are_owned_by_injected_client(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    window.panic()
    assert client.calls[-1] == ("panic", None)

    window.close()

    assert client.closed
    assert not window._preview_timer.isActive()
    assert not window._metrics_timer.isActive()
