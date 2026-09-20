"""Seek/scrub control behavior for Load Video sources in the inspector."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import SourceState, SourceStatus
from synesthesia_machine.ui.playback_controls import VideoPlaybackControls
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.widgets import InspectorPanel


def _capture_seeks(controls: VideoPlaybackControls) -> list[float]:
    emitted: list[float] = []
    controls.seekRequested.connect(emitted.append)
    return emitted


def test_controls_disabled_until_the_engine_reports_progress(qapp: QApplication) -> None:
    del qapp
    controls = VideoPlaybackControls()

    assert not controls.progress_slider.isEnabled()
    assert not controls.rewind_5_button.isEnabled()
    assert not controls.forward_15_button.isEnabled()

    controls.set_progress(1.0, 0.0, 10.0, 10.0)

    assert controls.progress_slider.isEnabled()
    assert controls.rewind_5_button.isEnabled()
    assert controls.forward_15_button.isEnabled()


def test_slider_release_seeks_to_the_scrubbed_position(qapp: QApplication) -> None:
    del qapp
    controls = VideoPlaybackControls()
    controls.set_progress(3.0, 0.0, 12.0, 12.0)
    emitted = _capture_seeks(controls)

    assert controls.progress_slider.value() == 2_500
    controls.progress_slider.setValue(5_000)
    controls.progress_slider.sliderReleased.emit()

    assert emitted == [6.0]


def test_nudge_buttons_seek_by_fifteen_and_five_seconds(qapp: QApplication) -> None:
    del qapp
    controls = VideoPlaybackControls()
    controls.set_progress(10.0, 0.0, 60.0, 60.0)
    emitted = _capture_seeks(controls)

    controls.forward_5_button.click()
    controls.forward_15_button.click()
    controls.rewind_5_button.click()
    controls.rewind_15_button.click()

    assert emitted == [15.0, 30.0, 25.0, 10.0]


def test_seeks_clamp_to_the_video_bounds(qapp: QApplication) -> None:
    del qapp
    controls = VideoPlaybackControls()
    controls.set_progress(5.0, 0.0, 60.0, 60.0)
    emitted = _capture_seeks(controls)

    controls.rewind_15_button.click()
    controls.forward_15_button.click()
    controls.forward_15_button.click()
    controls.forward_15_button.click()

    assert emitted == [0.0, 15.0, 30.0, 45.0]
    # 45 + 15 reaches the end of the video and stays clamped there.
    controls.forward_15_button.click()
    assert emitted[-1] == 60.0


def test_position_label_shows_current_and_total_time(qapp: QApplication) -> None:
    del qapp
    controls = VideoPlaybackControls()
    controls.set_progress(1.5, 0.0, 30.0, 30.0)

    label = controls.position_label
    assert isinstance(label, QLabel)
    assert "00:00:01.50" in label.text()
    assert "00:00:30" in label.text()


def test_slider_spans_the_published_region(qapp: QApplication) -> None:
    del qapp
    controls = VideoPlaybackControls()
    # The engine resolved the region to [10 s, 20 s): the slider must span
    # that interval, not [0, duration] (ADR-0028).
    controls.set_progress(15.0, 10.0, 20.0, 60.0)

    assert controls.progress_slider.value() == 5_000
    assert "00:00:20" in controls.position_label.text()
    emitted = _capture_seeks(controls)
    controls.forward_5_button.click()
    controls.forward_15_button.click()  # 25 -> clamped to the region end
    assert emitted == [20.0, 20.0]
    controls.rewind_15_button.click()  # 5 -> clamped to the region start
    assert emitted[-1] == 10.0
    # An unknown region end falls back to the container duration as the
    # scale's upper bound.
    controls.set_progress(5.0, 0.0, None, 10.0)
    assert controls.progress_slider.value() == 5_000


def test_inspector_shows_playback_controls_only_for_video_sources(qapp: QApplication) -> None:
    del qapp
    registry = create_application_registry()
    session = DocumentSession(registry)
    video_id = session.add_node("synmachine.input.load_video", (0.0, 0.0))
    gain_id = session.add_node("synmachine.utility.transpose", (0.0, 100.0))
    inspector = InspectorPanel(session)

    inspector.set_selection({video_id}, set())
    assert inspector.findChild(VideoPlaybackControls) is not None

    inspector.set_selection({gain_id}, set())
    assert inspector.findChild(VideoPlaybackControls) is None


def test_inspector_forwards_video_seeks_to_its_consumers(qapp: QApplication) -> None:
    del qapp
    registry = create_application_registry()
    session = DocumentSession(registry)
    video_id = session.add_node("synmachine.input.load_video", (0.0, 0.0))
    inspector = InspectorPanel(session)
    inspector.set_selection({video_id}, set())

    forwarded: list[tuple[object, float]] = []

    def record(node_id: object, position: float) -> None:
        forwarded.append((node_id, position))

    inspector.videoSeekRequested.connect(record)
    controls = inspector.findChild(VideoPlaybackControls)
    assert controls is not None
    controls.set_progress(10.0, 0.0, 60.0, 60.0)
    controls.forward_5_button.click()

    assert forwarded == [(video_id, 15.0)]


def test_inspector_refreshes_controls_from_source_statuses(qapp: QApplication) -> None:
    del qapp
    registry = create_application_registry()
    session = DocumentSession(registry)
    video_id = session.add_node("synmachine.input.load_video", (0.0, 0.0))
    other_id = session.add_node("synmachine.input.load_video", (0.0, 100.0))
    inspector = InspectorPanel(session)
    inspector.set_selection({video_id}, set())
    controls = inspector.findChild(VideoPlaybackControls)
    assert controls is not None

    inspector.set_source_statuses(
        (
            _status(other_id, 9.0, 30.0),
            _status(video_id, 3.0, 30.0),
        )
    )

    assert controls.progress_slider.value() == 1_000


def test_engine_telemetry_does_not_fight_a_slider_the_user_is_holding(
    qapp: QApplication,
) -> None:
    controls = VideoPlaybackControls()
    controls.show()
    qapp.processEvents()
    controls.set_progress(10.0, 0.0, 60.0, 60.0)

    slider = controls.progress_slider
    point = slider.mapToGlobal(QPoint(slider.width() // 2, slider.height() // 2))
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=point)
    held = slider.value()
    qapp.processEvents()

    # While the user holds the slider, engine telemetry must not move it.
    controls.set_progress(55.0, 0.0, 60.0, 60.0)
    qapp.processEvents()
    assert slider.value() == held

    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=point)
    controls.set_progress(55.0, 0.0, 60.0, 60.0)
    qapp.processEvents()
    assert slider.value() == round(55.0 / 60.0 * 10_000)


def _status(node_id: object, source_time_s: float | None, duration_s: float | None) -> SourceStatus:
    return SourceStatus(
        node_id=node_id,
        state=SourceState.PLAYING,
        file_path="clip.mp4",
        width=320,
        height=240,
        duration_s=duration_s,
        source_time_s=source_time_s,
        source_frame_index=None,
        processed_index=0,
        skipped_by_selection=0,
        dropped_before_processing=0,
        warnings=0,
        last_error=None,
        source_kind="video",
        display_name="clip.mp4",
        device_id=None,
        backend="pyav",
        negotiated_fps=None,
        reconnect_attempts=0,
        mailbox_occupancy=0,
        frame_age_ms=0.0,
        mailbox_capacity=2,
        processing_latency_ms=0.0,
        requested_width=None,
        requested_height=None,
        requested_fps=None,
    )
