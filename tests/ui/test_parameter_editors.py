"""Timestamp editor and timestamp formatting tests for video-time parameters."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    NodeMemoryDiagnostic,
    PortType,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.nodes import ParameterEditorHint, ParameterSpec
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.parameter_editors import (
    LoopKnob,
    LoopRangeParameterEditor,
    SiblingContext,
    TimestampParameterEditor,
    format_timestamp,
    parse_timestamp,
)
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME
from synesthesia_machine.ui.view_models import ParameterViewModel
from synesthesia_machine.ui.widgets import InspectorPanel


def _timestamp_editor(maximum: float | None) -> TimestampParameterEditor:
    spec = ParameterSpec(
        "loop_end_s",
        "Loop end",
        PortType.FLOAT,
        0.0,
        minimum=0.0,
        maximum=maximum,
        editor_hint=ParameterEditorHint.TIMESTAMP,
    )
    view_model = ParameterViewModel(spec=spec, value=0.0, connected=False)
    return TimestampParameterEditor(view_model, lambda _value: None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "00:00:00"),
        (35.64, "00:00:35.64"),
        (59.99, "00:00:59.99"),
        (60.0, "00:01:00"),
        (120.5, "00:02:00.50"),
        (300.0, "00:05:00"),
        (3600.0, "01:00:00"),
        (3661.5, "01:01:01.50"),
        (36_000.0, "10:00:00"),
    ],
)
def test_format_timestamp_normalises_minutes_and_hours(value: float, expected: str) -> None:
    assert format_timestamp(value) == expected


def test_format_timestamp_round_trips_through_parse() -> None:
    # Whatever the field displays must be text the field accepts again.
    for value in (0.0, 35.64, 120.5, 3600.0, 3661.5):
        assert parse_timestamp(format_timestamp(value)) == pytest.approx(value)


@pytest.mark.parametrize(
    ("maximum", "expected_maximum"),
    [(120.5, 120.5), (30.0, 30.0)],
)
def test_timestamp_slider_reaches_the_resolved_maximum(
    qapp: QApplication, maximum: float | None, expected_maximum: float
) -> None:
    """Dragging the slider to its end must reach the parameter's maximum.

    The slider spans the video's resolved duration — never a longer
    substitute range — so the far right of the slider is the end of the
    video itself.
    """

    del qapp
    editor = _timestamp_editor(maximum)
    editor.slider.setValue(editor.slider.maximum())
    assert parse_timestamp(editor.timestamp_field.text()) == pytest.approx(
        expected_maximum, abs=0.02
    )


def test_timestamp_field_displays_minutes_beyond_one_minute(qapp: QApplication) -> None:
    del qapp
    editor = _timestamp_editor(None)
    editor.setValue(300.0)
    assert editor.timestamp_field.text() == "00:05:00"


def _timestamp_editor_capturing(
    maximum: float | None,
) -> tuple[TimestampParameterEditor, list[float]]:
    """Timestamp editor whose on-changed callback records committed values."""
    spec = ParameterSpec(
        "loop_end_s",
        "Loop end",
        PortType.FLOAT,
        0.0,
        minimum=0.0,
        maximum=maximum,
        editor_hint=ParameterEditorHint.TIMESTAMP,
    )
    view_model = ParameterViewModel(spec=spec, value=0.0, connected=False)
    commits: list[float] = []
    return TimestampParameterEditor(view_model, commits.append), commits


def test_unbounded_timestamp_editor_is_inert(qapp: QApplication) -> None:
    # While the video's duration is unknown the editor must not present a
    # substitute scale: slider and field are disabled, so the user cannot
    # select a time that lies outside the video's real range.
    editor, commits = _timestamp_editor_capturing(None)
    assert not editor.slider.isEnabled()
    assert not editor.timestamp_field.isEnabled()
    # Even if a gesture reaches the widgets directly, nothing on the
    # substitute scale is committed and the display snaps back to the
    # pre-drag value.
    editor.slider.setValue(editor.slider.maximum())
    editor.slider.sliderReleased.emit()
    qapp.processEvents()
    assert commits == []
    assert parse_timestamp(editor.timestamp_field.text()) == pytest.approx(0.0)


def test_real_scale_drag_commits(qapp: QApplication) -> None:
    # With a known video duration the slider spans that duration, so knob
    # drags commit real-scaled values; the fallback guard must not suppress
    # them.
    editor, commits = _timestamp_editor_capturing(60.0)
    editor.slider.setValue(5000)
    editor.slider.sliderReleased.emit()
    qapp.processEvents()
    assert len(commits) == 1
    assert commits[0] == pytest.approx(30.0, abs=0.1)


def test_typed_value_on_real_scale_commits(qapp: QApplication) -> None:
    # A typed timestamp on the real scale is an explicit user value: it
    # commits against the video's duration (the engine still reports a
    # persisted out-of-range region as a clear error).
    del qapp
    editor, commits = _timestamp_editor_capturing(60.0)
    editor.timestamp_field.setText("00:00:30")
    editor.timestamp_field.editingFinished.emit()
    assert len(commits) == 1
    assert commits[0] == pytest.approx(30.0)


# -- rebuild safety during in-flight editor drags --------------------------
#
# During playback the engine publishes telemetry at ~10 Hz. A telemetry tick
# (or any session change) can rebuild the inspector form or the scene's node
# items. If that happens while the user is dragging a parameter editor, the
# dragged widget is destroyed mid-gesture: Python references keep pointing at
# deleted C++ objects (shiboken "already deleted" on the next tick) and queued
# mouse events / signals delivered to the freed widget crash the UI process
# natively. Rebuilds must therefore be deferred while a drag is in flight.


def _session_with_loop_video() -> tuple[DocumentSession, UUID]:
    """Session whose Load Video node has an engine-published duration.

    The loop editors resolve their scale from that published duration (the
    status's file must match the node's ``file_path``); without it they are
    inert, so every drag test needs the status to drive a real scale.
    """

    session = DocumentSession(create_application_registry())
    node_id = session.add_node(
        "synmachine.input.load_video",
        (0.0, 0.0),
        parameters={
            "file_path": "/tmp/loop-video-test.mp4",
            "loop": True,
            "loop_start_s": 0.3,
            "loop_end_s": 1.5,
        },
    )
    session.set_source_statuses(
        (
            SourceStatus(
                node_id,
                SourceState.PLAYING,
                file_path="/tmp/loop-video-test.mp4",
                duration_s=3.0,
                source_time_s=0.0,
            ),
        )
    )
    return session, node_id


def test_inspector_defers_rebuild_while_loop_drag_is_active(
    qapp: QApplication,
) -> None:
    """A telemetry tick mid-drag must not destroy the dragged loop editor.

    The tick forces a full form rebuild (the in-place diagnostic label path is
    unavailable), the drag must survive it, still commit on release, and the
    post-release rebuild plus further telemetry ticks must stay healthy (no
    stale playback-controls references).
    """

    session, node_id = _session_with_loop_video()
    inspector = InspectorPanel(session)
    inspector.set_selection({node_id}, set())
    editor = inspector.findChild(LoopRangeParameterEditor)
    assert editor is not None

    x = editor.x_for_position(editor.start_position())
    editor.knob_pressed(x, 30.0)
    assert editor.track is not None  # sanity: drag state is live

    # A 10 Hz telemetry tick for a node the in-place label path cannot serve:
    # before the fix this tore the form down mid-drag and the next access to
    # the deleted editor raised "Internal C++ object already deleted".
    other_node = uuid4()
    inspector.set_memory_diagnostic(NodeMemoryDiagnostic(other_node, estimated_retained_bytes=1))
    assert editor.track is not None

    editor.knob_moved(x + 12.0)
    editor.knob_released()
    qapp.processEvents()  # the drag-end re-scheduled refresh now lands
    assert float(session.document.node(node_id).parameters["loop_start_s"]) != pytest.approx(0.3)

    # Post-drag telemetry must rebuild cleanly and keep serving updates: the
    # playback controls must not be a stale reference to a deleted C++ object.
    inspector.set_memory_diagnostic(NodeMemoryDiagnostic(other_node, estimated_retained_bytes=4))
    inspector.set_source_statuses(
        (SourceStatus(node_id, SourceState.PLAYING, duration_s=3.0, source_time_s=0.5),)
    )


def test_scene_defers_node_rebuild_while_canvas_loop_drag_is_active(
    qapp: QApplication,
) -> None:
    """A session change mid-drag must not rebuild the node item being dragged.

    The scene would otherwise delete the QGraphicsProxyWidget (and the painted
    track holding the mouse gesture) while the drag is in flight; queued
    mouse events on the freed widget crash the UI process natively.
    """

    session, node_id = _session_with_loop_video()
    scene = GraphScene(session, DEFAULT_THEME)
    item = scene.node_items[node_id]
    item.setSelected(True)
    proxy = item.parameter_editors["loop_start_s"]
    editor = proxy.widget()

    x = editor.x_for_position(editor.start_position())
    editor.knob_pressed(x, 30.0)
    before = scene.node_items[node_id]

    # A view-model change while the drag is in flight (any session edit, or
    # the engine's first telemetry tick after playback starts).
    session.set_parameter(node_id, "process_every_nth_frame", 2)
    assert scene.node_items[node_id] is before
    assert scene.node_items[node_id].parameter_editors["loop_start_s"] is proxy

    editor.knob_moved(x + 40.0)
    editor.knob_released()
    qapp.processEvents()  # drag-end resync applies the deferred change
    assert float(session.document.node(node_id).parameters["loop_start_s"]) != pytest.approx(0.3)


def _shown_canvas_with_loop_node(
    qapp: QApplication,
) -> tuple[DocumentSession, UUID, GraphScene, GraphView]:
    session, node_id = _session_with_loop_video()
    scene = GraphScene(session, DEFAULT_THEME)
    view = GraphView(scene, DEFAULT_THEME)
    scene.node_items[node_id].setSelected(True)
    view.resize(800, 600)
    view.show()
    qapp.processEvents()
    return session, node_id, scene, view


def _viewport_point(view: GraphView, scene_pt: QPointF):
    mapped = view.mapFromScene(scene_pt)
    return mapped.toPoint() if hasattr(mapped, "toPoint") else mapped


def test_canvas_loop_drag_via_view_moves_knob_and_commits(qapp: QApplication) -> None:
    """A left-click drag on the canvas loop track must move the knob and commit.

    The view owns the gesture end to end: press/move/release are view-level
    events, the only delivery path guaranteed on every platform (a mouse grab
    from the proxy child widget is unreliable there). Before the fix the
    gesture was dead on the canvas and the release-time commit let the scene
    rebuild the editor mid-release-dispatch, crashing the process.
    """

    session, node_id, scene, view = _shown_canvas_with_loop_node(qapp)
    proxy = scene.node_items[node_id].parameter_editors["loop_start_s"]
    editor = proxy.widget()
    assert isinstance(editor, LoopRangeParameterEditor)
    press_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.start_position()), 30.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    assert editor.dragged_knob() is LoopKnob.START

    move_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.start_position()) + 60.0, 22.0))
    )
    QTest.mouseMove(view.viewport(), move_pt)
    qapp.processEvents()
    assert editor.start_value() != pytest.approx(0.3)

    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, move_pt
    )
    expected = editor.start_value()  # read before the drag-end rebuild frees the widget
    qapp.processEvents()
    assert float(session.document.node(node_id).parameters["loop_start_s"]) == pytest.approx(
        expected
    )


def test_canvas_loop_drag_leaving_the_view_commits(qapp: QApplication) -> None:
    """Losing the pointer mid-drag commits at the last in-view position."""

    session, node_id, scene, view = _shown_canvas_with_loop_node(qapp)
    proxy = scene.node_items[node_id].parameter_editors["loop_start_s"]
    editor = proxy.widget()
    assert isinstance(editor, LoopRangeParameterEditor)
    press_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.start_position()), 30.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    assert editor.dragged_knob() is LoopKnob.START
    view.leaveEvent(QEvent(QEvent.Type.Leave))
    expected = editor.start_value()
    assert view._loop_drag is None
    qapp.processEvents()
    assert float(session.document.node(node_id).parameters["loop_start_s"]) == pytest.approx(
        expected
    )


def test_canvas_loop_editor_displays_timestamps_below_track(qapp: QApplication) -> None:
    """The compact canvas editor shows start and end timestamps under the track."""

    session, node_id, scene, view = _shown_canvas_with_loop_node(qapp)
    proxy = scene.node_items[node_id].parameter_editors["loop_start_s"]
    editor = proxy.widget()
    assert isinstance(editor, LoopRangeParameterEditor)
    label = editor.compact_label
    assert label is not None
    assert label.isVisibleTo(view)
    assert label.text() == f"{format_timestamp(0.3)} \u2192 {format_timestamp(1.5)}"

    # A view-level drag updates the readout live.
    press_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.start_position()), 30.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    move_pt = _viewport_point(view, proxy.mapToScene(QPointF(60.0, 22.0)))
    QTest.mouseMove(view.viewport(), move_pt)
    qapp.processEvents()
    assert label.text() == editor.compact_value_text()
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, move_pt
    )
    expected_value = editor.start_value()
    qapp.processEvents()
    committed = float(session.document.node(node_id).parameters["loop_start_s"])
    assert committed == pytest.approx(expected_value)


# -- published-scale continuity and end-sentinel display -------------------


def _loop_range_editor_capturing(
    *,
    start: float,
    end: float,
    maximum: float | None,
    compact: bool = False,
    start_connected: bool = True,
    end_connected: bool = True,
) -> tuple[LoopRangeParameterEditor, list[object], list[tuple[str, object]]]:
    """Loop range editor with a known or unknown published scale."""

    start_spec = ParameterSpec(
        "loop_start_s",
        "Loop start",
        PortType.FLOAT,
        start,
        minimum=0.0,
        maximum=maximum,
        editor_hint=ParameterEditorHint.TIMESTAMP,
    )
    end_spec = ParameterSpec(
        "loop_end_s",
        "Loop end",
        PortType.FLOAT,
        end,
        minimum=0.0,
        maximum=maximum,
        editor_hint=ParameterEditorHint.TIMESTAMP,
    )
    view_model = ParameterViewModel(spec=start_spec, value=start, connected=start_connected)
    commits: list[object] = []
    sibling_edits: list[tuple[str, object]] = []
    editor = LoopRangeParameterEditor(
        view_model,
        commits.append,
        siblings=SiblingContext(
            values={"loop_end_s": end},
            connected={"loop_end_s": end_connected},
            specs={"loop_end_s": end_spec},
        ),
        on_sibling_changed=lambda parameter_id, value: sibling_edits.append((parameter_id, value)),
        compact=compact,
    )
    return editor, commits, sibling_edits


def test_loop_end_sentinel_displays_published_duration(qapp: QApplication) -> None:
    # The 0.0 end sentinel means "until the source ends": with a published
    # duration, both the compact canvas readout and the inspector field must
    # show the actual end of the video, not 00:00:00.
    del qapp
    compact = _loop_range_editor_capturing(start=30.0, end=0.0, maximum=120.5, compact=True)[0]
    assert compact._bounds_fallback is False
    assert (
        compact.compact_value_text() == f"{format_timestamp(30.0)} \u2192 {format_timestamp(120.5)}"
    )
    full = _loop_range_editor_capturing(start=30.0, end=0.0, maximum=120.5)[0]
    assert full.end_field is not None
    assert full.end_field.text() == format_timestamp(120.5)


def test_loop_end_sentinel_shown_raw_on_substitute_scale(qapp: QApplication) -> None:
    # Without a published duration the substitute scale's maximum is not a
    # duration, so the sentinel reads 00:00:00 rather than a fabricated end.
    del qapp
    editor = _loop_range_editor_capturing(start=0.0, end=0.0, maximum=None)[0]
    assert editor._bounds_fallback is True
    assert editor.compact_value_text() == f"{format_timestamp(0.0)} \u2192 {format_timestamp(0.0)}"


def test_loop_end_field_focus_out_without_edit_keeps_sentinel(qapp: QApplication) -> None:
    # The resolved duration shown in the end field is a display of the 0.0
    # sentinel: a focus-out without an edit must not commit it as a concrete
    # end, or swapping in a longer video would stop the loop short of the
    # new end. A typed value is an explicit choice and commits.
    del qapp
    editor, _, sibling_edits = _loop_range_editor_capturing(start=0.0, end=0.0, maximum=120.5)
    editor._flush_end_field()
    assert sibling_edits == []
    assert editor._end_s == 0.0
    assert editor.end_field is not None
    editor.end_field.setText("00:00:30")
    editor._flush_end_field()
    assert sibling_edits[-1] == ("loop_end_s", 30.0)


def test_canvas_loop_editor_keeps_published_scale_after_drag_commit(qapp: QApplication) -> None:
    """A drag commit re-projects the view model; the editor must keep the
    engine-published scale.

    If the re-projection dropped the source statuses, the rebuilt editor
    falls back to the substitute scale and becomes inert: every later click
    on the track is a no-op (the knob resets or sticks, and nothing moves).
    """

    session, node_id, scene, view = _shown_canvas_with_loop_node(qapp)
    proxy = scene.node_items[node_id].parameter_editors["loop_start_s"]
    editor = proxy.widget()
    press_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.start_position()), 22.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    move_pt = press_pt + QPoint(60, 0)
    QTest.mouseMove(view.viewport(), move_pt)
    qapp.processEvents()
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, move_pt
    )
    qapp.processEvents()  # the commit's refresh re-projects the view model

    rebuilt = scene.node_items[node_id].parameter_editors["loop_start_s"].widget()
    assert rebuilt._bounds_fallback is False
    assert rebuilt.track.isEnabled()

    # A second gesture must still move the knob and commit.
    before = rebuilt.start_value()
    proxy2 = scene.node_items[node_id].parameter_editors["loop_start_s"]
    press2 = _viewport_point(
        view, proxy2.mapToScene(QPointF(rebuilt.x_for_position(rebuilt.start_position()), 22.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press2
    )
    qapp.processEvents()
    assert rebuilt.dragged_knob() is LoopKnob.START
    move2 = press2 + QPoint(-30, 0)
    QTest.mouseMove(view.viewport(), move2)
    qapp.processEvents()
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, move2
    )
    qapp.processEvents()
    assert rebuilt.start_value() != pytest.approx(before)
    assert float(session.document.node(node_id).parameters["loop_start_s"]) != pytest.approx(0.3)


def test_canvas_loop_drag_stays_glued_to_pointer_across_view_reframe(qapp: QApplication) -> None:
    """A view reframe between the press and a move must not teleport the
    knob.

    The gesture is anchored in viewport space at press time: layout settling
    or recentering shifts the scene frame of a fixed viewport point, and
    re-mapping every move through the current frame landed the first move
    far outside the track, pinning the knob to the published maximum and
    committing an out-of-range region on release.
    """

    session, node_id, scene, view = _shown_canvas_with_loop_node(qapp)
    proxy = scene.node_items[node_id].parameter_editors["loop_start_s"]
    editor = proxy.widget()
    press_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.start_position()), 30.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    assert editor.dragged_knob() is LoopKnob.START

    # Reframe the view under the stationary pointer: the scene frame of any
    # fixed viewport point shifts by the translation.
    view.centerOn(QPointF(2000.0, 2000.0))
    qapp.processEvents()

    QTest.mouseMove(view.viewport(), press_pt + QPoint(10, 0))
    qapp.processEvents()
    # Ten viewport pixels is a small step on the track: the knob moves a
    # little, not to the far end of the published scale.
    assert editor.start_value() < 1.0
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    assert float(session.document.node(node_id).parameters["loop_start_s"]) < 1.0


# -- knob hitboxes: each lane of the track owns exactly one knob -----------
#
# The end knob is the down-pointing triangle above the groove, the start
# knob the up-pointing triangle below it. A press must only ever grab the
# knob on its own side: with an x-only hit test the start knob (checked
# first) stole presses aimed at the end knob whenever the two sat close
# together, as they do for any short loop region.


def test_upper_lane_press_grabs_end_knob_despite_close_start(qapp: QApplication) -> None:
    # A press on the end knob's side grabs the end knob even when the start
    # knob lies inside the old x-only grab window of the same press.
    del qapp
    editor, _, _ = _loop_range_editor_capturing(
        start=0.3, end=0.5, maximum=3.0, start_connected=False, end_connected=False
    )
    end_x = editor.x_for_position(editor.end_position())
    start_x = editor.x_for_position(editor.start_position())
    assert abs(end_x - start_x) <= 14.0  # the old grab windows overlap here
    editor.knob_pressed(end_x, 10.0)  # upper lane: the end knob's side
    assert editor.dragged_knob() is LoopKnob.END
    editor.knob_released()


def test_lower_lane_press_grabs_start_knob(qapp: QApplication) -> None:
    del qapp
    editor, _, _ = _loop_range_editor_capturing(
        start=0.3, end=0.5, maximum=3.0, start_connected=False, end_connected=False
    )
    start_x = editor.x_for_position(editor.start_position())
    editor.knob_pressed(start_x, 30.0)  # lower lane: the start knob's side
    assert editor.dragged_knob() is LoopKnob.START
    editor.knob_released()


def test_lane_press_drives_only_that_lanes_knob(qapp: QApplication) -> None:
    # A track press drives the knob on its own side only; the other knob is
    # left exactly where it was.
    del qapp
    editor, _, _ = _loop_range_editor_capturing(
        start=0.3, end=0.5, maximum=3.0, start_connected=False, end_connected=False
    )
    start_before = editor.start_value()
    editor.knob_pressed(12.0, 10.0)  # upper lane, far from the end knob
    assert editor.dragged_knob() is LoopKnob.END
    assert editor.start_value() == start_before
    editor.knob_released()


def test_disabled_lane_press_starts_no_drag(qapp: QApplication) -> None:
    # A press in the lane of a connected (disabled) knob starts no drag and
    # never steals across the groove into the other knob.
    del qapp
    editor, _, _ = _loop_range_editor_capturing(
        start=0.3, end=0.5, maximum=3.0, start_connected=True, end_connected=False
    )
    start_x = editor.x_for_position(editor.start_position())
    editor.knob_pressed(start_x, 30.0)  # lower lane, but the start knob is disabled
    assert editor.dragged_knob() is None
    # The same press on the end knob's side still works.
    end_x = editor.x_for_position(editor.end_position())
    editor.knob_pressed(end_x, 10.0)
    assert editor.dragged_knob() is LoopKnob.END
    editor.knob_released()


def test_coincident_knobs_split_by_lane(qapp: QApplication) -> None:
    # Both knobs on the same x: the lane, not the x, decides the grab.
    del qapp
    editor, _, _ = _loop_range_editor_capturing(
        start=0.3, end=0.5, maximum=3.0, start_connected=False, end_connected=False
    )
    # Collapse the region onto one knob: with a concrete end the scale spans
    # [0..end], so both knobs sit on the far right of the track.
    editor.set_end(editor.start_value(), commit=False)
    x = editor.x_for_position(editor.start_position())
    editor.knob_pressed(x, 10.0)
    assert editor.dragged_knob() is LoopKnob.END
    editor.knob_released()
    editor.knob_pressed(x, 30.0)
    assert editor.dragged_knob() is LoopKnob.START
    editor.knob_released()


def test_canvas_upper_lane_press_drags_end_knob(qapp: QApplication) -> None:
    """A view-level press in the upper lane drags the end knob.

    The lane must survive the view's proxy mapping: the view feeds the
    press's y coordinate into the editor, so a press on the end knob is not
    stolen by the start knob when the region is short.
    """

    session, node_id, scene, view = _shown_canvas_with_loop_node(qapp)
    # Pull the start knob within the end knob's old x-only grab window:
    # with the concrete end the scale spans [0..1.5s].
    session.set_parameter(node_id, "loop_start_s", 1.3)
    qapp.processEvents()  # the re-projection may rebuild the editor
    item = scene.node_items[node_id]
    proxy = item.parameter_editors["loop_start_s"]
    editor = proxy.widget()
    assert isinstance(editor, LoopRangeParameterEditor)
    press_pt = _viewport_point(
        view, proxy.mapToScene(QPointF(editor.x_for_position(editor.end_position()), 10.0))
    )
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
    assert editor.dragged_knob() is LoopKnob.END
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_pt
    )
    qapp.processEvents()
