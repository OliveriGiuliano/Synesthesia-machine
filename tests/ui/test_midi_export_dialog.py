"""File > Export MIDI: action gating, in-window overlay, worker wiring."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from tools.generate_test_video import HUE_FRAME_COUNT, generate_hue_test_video

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.runtime import InProcessEngineClient
from synesthesia_machine.ui.main_window import MainWindow
from synesthesia_machine.ui.midi_export import MidiExportJob


def _paths(root: Path) -> ApplicationPaths:
    data = root / "data"
    return ApplicationPaths(data, data / "logs", data / "recovery")


def _settings(root: Path) -> QSettings:
    return QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def video_window(qapp: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """A window over the full registry with a generated video on disk."""

    video = tmp_path / "hue.mp4"
    generate_hue_test_video(video, width=64, height=64, frame_count=HUE_FRAME_COUNT, fps=12)
    registry = create_application_registry()
    window = MainWindow(
        registry,
        _paths(tmp_path),
        InProcessEngineClient(registry),
        settings=_settings(tmp_path),
        offer_recovery=False,
    )
    window.show()
    qapp.processEvents()
    yield window
    if window._midi_export_job is not None:
        window._midi_export_job.cancel()
        window._midi_export_job.close()
    window.session.new_document()
    window.close()


def _export_action(window: MainWindow):
    return window.action_registry.require("export_midi")


def _wire_valid_graph(window: MainWindow, video: str) -> None:
    document = window.session.document
    source = document.add_node("synmachine.input.load_video", parameters={"file_path": video})
    luminance = document.add_node("synmachine.image.to_luminance")
    pitch = document.add_node("synmachine.synesthesia.channel_to_pitch")
    send = document.add_node("synmachine.output.send_midi")
    document.add_connection(source, "image", luminance, "image")
    document.add_connection(luminance, "channel", pitch, "value")
    document.add_connection(pitch, "midi", send, "midi")
    window.session._refresh()


def test_export_action_is_disabled_without_any_source(video_window: MainWindow) -> None:
    video_window._refresh_action_states()
    action = _export_action(video_window)
    assert not action.isEnabled()
    assert "load video" in action.toolTip().lower()


def test_export_action_enables_for_a_valid_video_to_midi_graph(
    video_window: MainWindow, tmp_path: Path
) -> None:
    _wire_valid_graph(video_window, str(tmp_path / "hue.mp4"))
    video_window._refresh_action_states()
    assert _export_action(video_window).isEnabled()


def test_export_action_reports_the_most_actionable_reason(video_window: MainWindow) -> None:
    document = video_window.session.document
    document.add_node("synmachine.output.send_midi")
    video_window.session._refresh()
    video_window._refresh_action_states()
    assert not _export_action(video_window).isEnabled()
    assert "load video" in _export_action(video_window).toolTip().lower()

    # A missing video file yields a more specific reason than the generic
    # invalid-graph text.
    document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": "/nonexistent/export-midi-test.mp4"},
    )
    video_window.session._refresh()
    video_window._refresh_action_states()
    assert not _export_action(video_window).isEnabled()
    assert "video file" in _export_action(video_window).toolTip().lower()


def test_camera_source_disables_the_action(video_window: MainWindow, tmp_path: Path) -> None:
    _wire_valid_graph(video_window, str(tmp_path / "hue.mp4"))
    document = video_window.session.document
    document.add_node("synmachine.input.load_camera")
    video_window.session._refresh()
    video_window._refresh_action_states()
    assert not _export_action(video_window).isEnabled()
    assert "camera" in _export_action(video_window).toolTip().lower()


def test_export_completion_closes_the_overlay_and_reports_the_file(
    video_window: MainWindow, qapp: QApplication, tmp_path: Path
) -> None:
    _wire_valid_graph(video_window, str(tmp_path / "hue.mp4"))
    out_path = str(tmp_path / "out.mid")

    job = MidiExportJob(video_window, video_window.session.document.snapshot(), out_path)
    assert job.overlay.isVisible()

    job._worker = lambda snapshot, file_path: (
        job.signals.progress.emit(3, 10),
        job.signals.completed.emit(file_path),
    )
    video_window._midi_export_job = job
    job.signals.progress.connect(video_window._on_midi_export_progress)
    job.signals.completed.connect(video_window._on_midi_export_completed)
    job.signals.failed.connect(video_window._on_midi_export_failed)
    job.start(video_window.session.document.snapshot())

    start = time.monotonic()
    while video_window._midi_export_job is not None and time.monotonic() - start < 3.0:
        qapp.processEvents()
    assert video_window._midi_export_job is None
    assert out_path in video_window.statusBar().currentMessage()


def test_export_failure_shows_the_translated_failure_text(
    video_window: MainWindow, qapp: QApplication
) -> None:
    video_window._midi_export_job = None
    job = MidiExportJob(video_window, video_window.session.document.snapshot(), "/tmp/x.mid")
    job._worker = lambda snapshot, file_path: job.signals.failed.emit("cancelled", "stopped")
    video_window._midi_export_job = job
    job.signals.progress.connect(video_window._on_midi_export_progress)
    job.signals.completed.connect(video_window._on_midi_export_completed)
    job.signals.failed.connect(video_window._on_midi_export_failed)
    job.start(video_window.session.document.snapshot())

    start = time.monotonic()
    while video_window._midi_export_job is not None and time.monotonic() - start < 3.0:
        qapp.processEvents()
    assert video_window._midi_export_job is None
    assert "cancelled" in video_window.statusBar().currentMessage().lower()


def test_looping_video_source_blocks_export(video_window: MainWindow, tmp_path: Path) -> None:
    from synesthesia_machine.ui.midi_export import midi_export_eligibility

    document = video_window.session.document
    source = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(tmp_path / "hue.mp4"), "loop": True},
    )
    send = document.add_node("synmachine.output.send_midi")
    document.add_connection(source, "image", send, "midi")
    video_window.session._refresh()
    video_window._refresh_action_states()
    action = _export_action(video_window)
    assert not action.isEnabled()
    assert "loop" in action.toolTip().lower()
    snapshot = document.snapshot()
    assert midi_export_eligibility(snapshot, video_window.registry, graph_valid=True) == (
        False,
        "looping_source",
    )


def test_escape_does_not_dismiss_the_export_overlay(
    video_window: MainWindow, qapp: QApplication, tmp_path: Path
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    _wire_valid_graph(video_window, str(tmp_path / "hue.mp4"))
    job = MidiExportJob(
        video_window, video_window.session.document.snapshot(), str(tmp_path / "out.mid")
    )
    assert job.overlay.isVisible()
    QTest.keyClick(job.overlay, Qt.Key.Key_Escape)
    # Escape must not dismiss the overlay: the export keeps running and the
    # Cancel button is the only way back.
    assert job.overlay.isVisible()
    job.cancel()
    job.close()


def test_export_overlay_is_an_in_window_surface_not_a_separate_top_level_window(
    video_window: MainWindow,
) -> None:
    # Regression: the pre-fix implementation was a frameless window-modal
    # top-level dialog. On Wayland that separate surface can lose input
    # routing (Cancel does nothing) and its presentation stalled the UI
    # event thread: the worker finished and wrote the .mid file, but the
    # overlay stayed frozen at 100% with no completion and no way out. The
    # overlay must therefore be a child of the main window.
    job = MidiExportJob(
        video_window, video_window.session.document.snapshot(), "/tmp/never-written.mid"
    )
    assert job.overlay.window() is video_window
    job.overlay.hide()


def test_cancel_shows_cancelling_feedback(video_window: MainWindow, tmp_path: Path) -> None:
    from synesthesia_machine.ui.translations import tr

    job = MidiExportJob(
        video_window, video_window.session.document.snapshot(), str(tmp_path / "out.mid")
    )
    job.cancel()
    # Clicking Cancel must have a visible effect immediately, even though
    # the worker honours the stop flag a few seconds later.
    assert job.overlay.status_label.text() == tr("Cancelling…")
    assert not job.overlay.cancel_button.isEnabled()
    job.close()


def test_stale_export_signal_ignores_a_superseded_job(
    video_window: MainWindow, qapp: QApplication, tmp_path: Path
) -> None:
    # A worker outlives close() when the bounded shutdown phases exceed the
    # 5-second join timeout; its late signal must not tear down a newer
    # export that started in the meantime.
    stale = MidiExportJob(
        video_window, video_window.session.document.snapshot(), str(tmp_path / "stale.mid")
    )
    video_window._midi_export_job = stale
    stale.signals.progress.connect(video_window._on_midi_export_progress)
    stale.signals.completed.connect(video_window._on_midi_export_completed)
    stale.signals.failed.connect(video_window._on_midi_export_failed)

    fresh = MidiExportJob(
        video_window, video_window.session.document.snapshot(), str(tmp_path / "fresh.mid")
    )
    video_window._midi_export_job = fresh
    fresh.signals.progress.connect(video_window._on_midi_export_progress)
    fresh.signals.completed.connect(video_window._on_midi_export_completed)
    fresh.signals.failed.connect(video_window._on_midi_export_failed)

    message_before = video_window.statusBar().currentMessage()
    stale.signals.failed.emit("cancelled", "late")
    qapp.processEvents()

    assert video_window._midi_export_job is fresh
    assert fresh.overlay.isVisible()
    assert video_window.statusBar().currentMessage() == message_before


def _point_luma(window: MainWindow, x: int, y: int) -> int:
    color = window.grab().toImage().pixelColor(x, y)
    return color.red() + color.green() + color.blue()


def test_export_overlay_dims_the_whole_window(
    video_window: MainWindow, qapp: QApplication, tmp_path: Path
) -> None:
    # Regression: the overlay's dimming used to be a stylesheet rgba
    # background on a plain QWidget, which was not reliably composited over
    # the application's content, leaving the window locked but undimmed. The
    # dimming is now painted in the overlay's paintEvent and must darken the
    # whole window, not just the band around the progress bar.
    video_window.resize(1280, 800)
    qapp.processEvents()

    points = [(640, 8), (640, 792), (320, 400), (1232, 400)]
    before = [_point_luma(video_window, x, y) for x, y in points]
    job = MidiExportJob(
        video_window, video_window.session.document.snapshot(), str(tmp_path / "out.mid")
    )
    for _ in range(10):
        qapp.processEvents()
    for pre, (x, y) in zip(before, points, strict=True):
        post = _point_luma(video_window, x, y)
        assert post < pre - 8, f"point ({x}, {y}) not dimmed: {pre} -> {post}"
    job.close()


def test_disabled_menu_items_render_visually_greyed(
    video_window: MainWindow, qapp: QApplication
) -> None:
    # The theme stylesheet sets a color on QMenu, which suppresses Qt's
    # default disabled dimming. Without an explicit QMenu::item:disabled rule
    # the disabled "Export MIDI" item looks identical to an enabled item, so
    from PySide6.QtWidgets import QMenu

    menus = video_window.menuBar().findChildren(QMenu)
    file_menu = next(menu for menu in menus if menu.title().replace("&", "").startswith("File"))
    export_action = _export_action(video_window)
    assert not export_action.isEnabled()

    file_menu.show()
    for _ in range(10):
        qapp.processEvents()

    def item_peak_luma(menu, action) -> int:
        image = menu.grab().toImage()
        rect = menu.actionGeometry(action)
        peak = 0
        for y in range(max(0, rect.y() + 3), min(image.height(), rect.y() + rect.height() - 3)):
            for x in range(12, min(image.width(), rect.x() + rect.width() - 30)):
                color = image.pixelColor(x, y)
                peak = max(peak, color.red() + color.green() + color.blue())
        return peak

    disabled_peak = item_peak_luma(file_menu, export_action)
    enabled_peak = item_peak_luma(file_menu, video_window.action_registry.require("exit"))
    file_menu.close()
    # Themed text (#e7ebf0 ≈ 706 luma) vs disabled grey (#59616c ≈ 294).
    assert disabled_peak < enabled_peak - 200
