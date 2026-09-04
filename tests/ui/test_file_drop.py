"""Operating-system file drops on the graph editor.

Dropping a video file creates a Load Video node pre-wired to the dropped file;
dropping a saved graph file opens it through the File > Open flow.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes.input.video import LOAD_VIDEO_TYPE_ID
from synesthesia_machine.persistence import save_graph
from synesthesia_machine.runtime import InProcessEngineClient
from synesthesia_machine.ui.canvas import SCENE_EXTENT_X, SCENE_EXTENT_Y
from synesthesia_machine.ui.main_window import MainWindow

SAMPLE_VIDEO = (
    Path(__file__).resolve().parents[2] / "examples" / "library" / "media" / "reference.mp4"
)


def _mime_with_file(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def _drop_on_view(view, file_path: Path | None, position: QPointF | QPoint) -> None:
    mime = QMimeData() if file_path is None else _mime_with_file(file_path)
    event = QDropEvent(
        position,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.dropEvent(event)
    assert event.isAccepted()


@pytest.fixture
def window(qapp: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    del qapp
    data = tmp_path / "data"
    paths = ApplicationPaths(data, data / "logs", data / "recovery")
    result = MainWindow(
        create_application_registry(),
        paths,
        InProcessEngineClient(create_application_registry()),
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    yield result
    result.session.new_document()
    result.close()


def test_dragging_a_video_file_is_accepted_and_others_are_not(
    window: MainWindow, tmp_path: Path
) -> None:
    video = tmp_path / "clip.mp4"
    shutil.copyfile(SAMPLE_VIDEO, video)
    notes = tmp_path / "notes.txt"
    notes.write_text("not media", encoding="utf-8")

    accepted = QMimeData()
    accepted.setUrls([QUrl.fromLocalFile(str(video))])
    enter = QDragEnterEvent(
        QPoint(0, 0),
        Qt.DropAction.CopyAction,
        accepted,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.view.dragEnterEvent(enter)
    assert enter.isAccepted()

    rejected = QMimeData()
    rejected.setUrls([QUrl.fromLocalFile(str(notes))])
    enter_reject = QDragEnterEvent(
        QPoint(0, 0),
        Qt.DropAction.CopyAction,
        rejected,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.view.dragEnterEvent(enter_reject)
    assert not enter_reject.isAccepted()


def test_video_file_drop_creates_wired_load_video_node(window: MainWindow, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    shutil.copyfile(SAMPLE_VIDEO, video)

    _drop_on_view(window.view, video, QPointF(120.0, 80.0))

    nodes = window.session.document.nodes
    assert len(nodes) == 1
    node = nodes[0]
    assert node.type_id == LOAD_VIDEO_TYPE_ID
    assert Path(node.parameters["file_path"]) == video  # type: ignore[index]
    assert node.position == (120.0, 80.0)  # type: ignore[union-attr]

    # The whole drop is one undo step.
    window.session.undo_stack.undo()
    assert window.session.document.nodes == ()


def test_video_dropped_beyond_the_edge_is_clamped_inside_the_graph_area(
    window: MainWindow, tmp_path: Path
) -> None:
    video = tmp_path / "clip.mp4"
    shutil.copyfile(SAMPLE_VIDEO, video)
    target_view = window.view.mapFromScene(
        QPointF(SCENE_EXTENT_X + 5000.0, SCENE_EXTENT_Y + 5000.0)
    )

    _drop_on_view(window.view, video, target_view)

    node = window.session.document.nodes[0]
    assert node.type_id == LOAD_VIDEO_TYPE_ID
    position = node.position
    rect = window.scene.sceneRect()
    assert rect.left() <= position[0]
    assert rect.top() <= position[1]
    assert position[0] < rect.right()
    assert position[1] < rect.bottom()


def test_non_video_file_drop_is_ignored(window: MainWindow, tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not media", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(notes))])
    event = QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.view.dropEvent(event)
    assert not event.isAccepted()
    assert window.session.document.nodes == ()


def test_graph_file_drop_not_accepted_when_the_open_is_cancelled(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The OS drag session must only be told the drop succeeded when the
    # open flow committed; a cancelled replacement leaves the current
    # document untouched and the drop unaccepted.
    from synesthesia_machine.ui.main_window import _ReplacementDecision

    document = GraphDocument()
    document.add_node("synmachine.utility.number", position=(55.0, 65.0))
    graph_path = tmp_path / "dropped-cancelled.synmachine.json"
    save_graph(graph_path, document.snapshot(), retain_backup=False)

    monkeypatch.setattr(
        window, "_confirm_document_replacement", lambda: _ReplacementDecision.CANCEL
    )
    mime = _mime_with_file(graph_path)
    event = QDropEvent(
        QPointF(0.0, 0.0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.view.dropEvent(event)
    assert not event.isAccepted()
    # The open was cancelled, so the window's document stays untouched
    # (still the fresh empty document, not the dropped graph).
    assert window.session.document.nodes == ()


def test_graph_file_drop_opens_the_saved_graph(window: MainWindow, tmp_path: Path) -> None:
    document = GraphDocument()
    document.add_node("synmachine.utility.number", position=(55.0, 65.0))
    graph_path = tmp_path / "dropped.synmachine.json"
    save_graph(graph_path, document.snapshot(), retain_backup=False)

    _drop_on_view(window.view, graph_path, QPointF(0.0, 0.0))

    loaded = window.session.document
    assert loaded is not document
    assert [node.type_id for node in loaded.nodes] == ["synmachine.utility.number"]
    assert loaded.nodes[0].position == (55.0, 65.0)
