"""EngineClient-only editor activation, transport, status, preview, and close tests."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QToolButton

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    MidiOutputConnectionState,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NoteActivity,
    NotePreview,
    SourceState,
    SourceStatus,
    freeze_uint8_preview,
)
from synesthesia_machine.graph import GraphSnapshot, ValidationReport
from synesthesia_machine.ui.main_window import MainWindow
from synesthesia_machine.ui.previews import NotePreviewWidget, note_rainbow_color

SOURCE_A = UUID("00000000-0000-0000-0000-000000000701")
SOURCE_B = UUID("00000000-0000-0000-0000-000000000702")
IMAGE_NODE = UUID("00000000-0000-0000-0000-000000000703")
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
    memory_diagnostics: tuple[NodeMemoryDiagnostic, ...] = ()
    midi_statuses: tuple[MidiOutputStatus, ...] = ()
    closed: bool = False

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        del demand_roots
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

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        if node_id is None:
            return self.memory_diagnostics
        return tuple(
            diagnostic for diagnostic in self.memory_diagnostics if diagnostic.node_id == node_id
        )

    def metrics(self) -> EngineMetrics:
        return EngineMetrics(
            EngineState.RUNNING,
            graph_revision=4,
            processed_ticks=42,
            processed_fps=29.5,
            p95_node_time_ms=2.25,
            dropped_before_processing=3,
            memory_bytes=64 * 1024 * 1024,
        )

    def poll_image_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.image_previews
            if preview.sequence > thresholds.get(preview.node_id, 0)
        )

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.note_previews
            if preview.sequence > thresholds.get(preview.node_id, 0)
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
    data = freeze_uint8_preview(np.full((2, 3, 3), 127, dtype=np.uint8))
    image = ImagePreview(IMAGE_NODE, 1, 7, 3, 2, 3, data)
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
    assert window._image_sequences == {IMAGE_NODE: 1}
    assert window._note_sequences == {NOTE_NODE: 1}
    assert "42 ticks" in window._engine_status.text()
    assert "in/process/preview 0.0/29.5/0.0 FPS" in window._engine_status.text()
    assert "drops 3" in window._engine_status.text()


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
