"""Canvas groups, comments, and exact command semantics."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QWidget

from synesthesia_machine.graph import GraphDocument, GroupKind, GroupModel
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import graph_from_json, graph_to_json
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.commands import (
    AddGroupCommand,
    DeleteGroupsCommand,
    EditGroupCommand,
    MoveGroupsCommand,
)
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME

GROUP_ID = UUID("70000000-0000-0000-0000-000000000001")
COMMENT_ID = UUID("70000000-0000-0000-0000-000000000002")


def _state(document: GraphDocument) -> tuple[object, ...]:
    snapshot = document.snapshot()
    return snapshot.document_id, snapshot.nodes, snapshot.connections, snapshot.groups


def _cycle(
    stack: QUndoStack,
    document: GraphDocument,
    before: tuple[object, ...],
    after: tuple[object, ...],
) -> None:
    for _ in range(3):
        stack.undo()
        assert _state(document) == before
        stack.redo()
        assert _state(document) == after


def test_group_and_comment_persist_canonically_with_strict_validation() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    document.add_group(
        GroupKind.GROUP,
        group_id=GROUP_ID,
        title="Analysis",
        position=(10.0, 20.0),
        size=(640.0, 360.0),
        color="#345678",
    )
    document.add_group(
        GroupKind.COMMENT,
        group_id=COMMENT_ID,
        title="Signal note",
        text="Keep this branch in the same source clock.",
        position=(30.0, 40.0),
        size=(320.0, 140.0),
        color="#765432",
    )

    text = graph_to_json(document.snapshot())
    restored = graph_from_json(text, registry)
    assert restored.groups == document.groups
    assert graph_to_json(restored) == text
    assert [item["kind"] for item in json.loads(text)["groups"]] == ["GROUP", "COMMENT"]

    malformed = json.loads(text)
    malformed["groups"][0]["size"] = [0.0, 10.0]
    with pytest.raises(ValueError, match="Group size must be positive"):
        graph_from_json(json.dumps(malformed), registry)


@pytest.mark.parametrize(
    "overrides",
    (
        {"position": (float("nan"), 0.0)},
        {"size": (-1.0, 10.0)},
        {"color": "blue"},
    ),
)
def test_group_domain_rejects_invalid_geometry_and_color(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "id": GROUP_ID,
        "kind": GroupKind.GROUP,
        "title": "Group",
        "text": "",
        "position": (0.0, 0.0),
        "size": (100.0, 100.0),
        "color": "#112233",
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        GroupModel(**values)  # type: ignore[arg-type]


def test_every_group_command_has_exact_repeated_undo_redo() -> None:
    document = GraphDocument()
    stack = QUndoStack()
    group = GroupModel(
        GROUP_ID,
        GroupKind.GROUP,
        "Sources",
        "",
        (0.0, 0.0),
        (400.0, 240.0),
        "#334455",
    )

    empty = _state(document)
    stack.push(AddGroupCommand(document, group))
    added = _state(document)
    _cycle(stack, document, empty, added)

    before_move = _state(document)
    stack.push(MoveGroupsCommand(document, {GROUP_ID: (0.0, 0.0)}, {GROUP_ID: (32.0, 48.0)}))
    moved = _state(document)
    _cycle(stack, document, before_move, moved)

    current = document.group(GROUP_ID)
    assert current is not None
    before_edit = _state(document)
    stack.push(
        EditGroupCommand(
            document,
            replace(current, title="Inputs", text="Camera and deterministic video sources."),
        )
    )
    edited = _state(document)
    _cycle(stack, document, before_edit, edited)

    before_delete = _state(document)
    stack.push(DeleteGroupsCommand(document, {GROUP_ID}))
    deleted = _state(document)
    assert not document.groups
    _cycle(stack, document, before_delete, deleted)


def test_session_scene_projects_selects_moves_and_deletes_groups(qapp: QApplication) -> None:
    del qapp
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.COMMENT,
        (12.0, 24.0),
        title="Remember",
        text="Inspect errors before playback.",
        size=(300.0, 120.0),
    )

    assert group_id in scene.group_items
    scene.select_group_ids({group_id})
    assert scene.selected_group_ids() == {group_id}
    session.move_groups({group_id: (12.0, 24.0)}, {group_id: (44.0, 88.0)})
    assert session.document.group(group_id).position == (44.0, 88.0)  # type: ignore[union-attr]

    scene.select_group_ids({group_id})
    scene.delete_selection()
    assert session.document.group(group_id) is None
    session.undo_stack.undo()
    assert session.document.group(group_id) is not None


def test_group_border_drag_resizes_and_is_undoable(qapp: QApplication) -> None:
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.GROUP,
        (0.0, 0.0),
        title="Resizable",
        size=(300.0, 120.0),
    )
    item = scene.group_items[group_id]
    item.setSelected(True)
    bounds = item.boundingRect()
    assert bounds.contains(QPointF(-3.0, -3.0))
    assert bounds.contains(QPointF(303.0, 123.0))
    view = GraphView(scene, DEFAULT_THEME)
    view.resize(800, 500)
    view.show()
    view.centerOn(item)
    qapp.processEvents()

    right_edge = view.mapFromScene(QPointF(299.0, 60.0))
    destination = QPoint(right_edge.x() + 80, right_edge.y())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=right_edge)
    QTest.mouseMove(view.viewport(), destination, delay=20)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    qapp.processEvents()

    resized = session.document.group(group_id)
    assert resized is not None
    assert resized.size[0] >= 375.0
    assert resized.size[1] == pytest.approx(120.0)
    session.undo_stack.undo()
    restored = session.document.group(group_id)
    assert restored is not None
    assert restored.size == (300.0, 120.0)
    view.close()


def test_group_drag_sticks_to_overlapping_nodes(qapp: QApplication) -> None:
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.GROUP, (100.0, 100.0), title="Batch", size=(400.0, 300.0)
    )
    inside = session.add_node("synmachine.utility.number", (130.0, 130.0))
    outside = session.add_node("synmachine.utility.number", (600.0, 400.0))
    view = GraphView(scene, DEFAULT_THEME)
    view.resize(900, 600)
    view.show()
    qapp.processEvents()

    press_scene = QPointF(450.0, 200.0)
    node_item = scene.node_items[inside]
    top_left = node_item.mapToScene(QPointF(0.0, 0.0))
    node_rect = QRectF(
        top_left, QSizeF(node_item.boundingRect().width(), node_item.boundingRect().height())
    )
    assert not node_rect.contains(press_scene)

    press = view.mapFromScene(press_scene)
    destination = press + QPoint(120, 0)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=press)
    QTest.mouseMove(view.viewport(), destination)
    qapp.processEvents()
    # While the drag is in flight the overlapping node follows the group.
    in_flight = scene.node_items[inside].pos()
    assert (in_flight.x(), in_flight.y()) == pytest.approx((250.0, 130.0))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    qapp.processEvents()

    moved = session.document.node(inside)
    assert moved is not None and moved.position == (250.0, 130.0)
    stayed = session.document.node(outside)
    assert stayed is not None and stayed.position == (600.0, 400.0)
    group = session.document.group(group_id)
    assert group is not None and group.position == pytest.approx((220.0, 100.0))
    view.close()


def test_group_drag_with_stuck_nodes_is_a_single_undo_step(qapp: QApplication) -> None:
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.GROUP, (100.0, 100.0), title="Batch", size=(400.0, 300.0)
    )
    inside = session.add_node("synmachine.utility.number", (130.0, 130.0))
    view = GraphView(scene, DEFAULT_THEME)
    view.resize(900, 600)
    view.show()
    qapp.processEvents()

    press = view.mapFromScene(QPointF(450.0, 200.0))
    destination = press + QPoint(120, 0)
    depth_before = session.undo_stack.index()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=press)
    QTest.mouseMove(view.viewport(), destination)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    qapp.processEvents()

    # One gesture, one undo step: the group move and its stuck node join a
    # single macro instead of committing two commands.
    assert session.undo_stack.index() == depth_before + 1

    session.undo_stack.undo()
    node = session.document.node(inside)
    assert node is not None and node.position == (130.0, 130.0)
    group = session.document.group(group_id)
    assert group is not None and group.position == (100.0, 100.0)

    session.undo_stack.redo()
    node = session.document.node(inside)
    assert node is not None and node.position == pytest.approx((250.0, 130.0))
    group = session.document.group(group_id)
    assert group is not None and group.position == pytest.approx((220.0, 100.0))
    view.close()


def test_plain_group_drag_is_still_one_undo_step(qapp: QApplication) -> None:
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    session.add_group(GroupKind.GROUP, (100.0, 100.0), title="Batch", size=(400.0, 300.0))
    view = GraphView(scene, DEFAULT_THEME)
    view.resize(900, 600)
    view.show()
    qapp.processEvents()

    press = view.mapFromScene(QPointF(450.0, 200.0))
    destination = press + QPoint(120, 0)
    depth_before = session.undo_stack.index()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=press)
    QTest.mouseMove(view.viewport(), destination)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    qapp.processEvents()

    assert session.undo_stack.index() == depth_before + 1
    view.close()


def test_group_drag_with_shift_held_does_not_stick_to_nodes(qapp: QApplication) -> None:
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.GROUP, (100.0, 100.0), title="Batch", size=(400.0, 300.0)
    )
    inside = session.add_node("synmachine.utility.number", (130.0, 130.0))
    view = GraphView(scene, DEFAULT_THEME)
    view.resize(900, 600)
    view.show()
    qapp.processEvents()

    press = view.mapFromScene(QPointF(450.0, 200.0))
    destination = press + QPoint(120, 0)
    QTest.mousePress(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=press,
    )
    QTest.mouseMove(view.viewport(), destination)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    qapp.processEvents()

    node = session.document.node(inside)
    assert node is not None and node.position == (130.0, 130.0)
    group = session.document.group(group_id)
    assert group is not None and group.position == pytest.approx((220.0, 100.0))
    view.close()


def test_group_rename_dialogs_parent_the_active_window_and_esc_renames_nothing(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A parentless rename dialog can leave the editor de-activated after ESC on
    # Wayland sessions (no input reaches an unfocused surface); the dialogs must
    # be parented to the active top-level window like every other dialog.
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.GROUP,
        (0.0, 0.0),
        title="Rename me",
        size=(300.0, 120.0),
    )
    view = GraphView(scene, DEFAULT_THEME)
    view.resize(800, 500)
    view.show()
    view.activateWindow()
    view.centerOn(scene.group_items[group_id])
    qapp.processEvents()

    dialog_parents: list[QWidget | None] = []

    def fake_get_text(
        parent: QWidget | None, title: str, label: str, **kwargs: object
    ) -> tuple[str, bool]:
        dialog_parents.append(parent)
        return "", False  # ESC: the user cancels the rename

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))

    center = view.mapFromScene(QPointF(150.0, 60.0))
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    qapp.processEvents()

    assert len(dialog_parents) == 1
    assert dialog_parents[0] is not None
    assert dialog_parents[0].window() is view

    # ESC cancelled the rename: the group is untouched and the second dialog
    # (comment text) never opened.
    group = session.document.group(group_id)
    assert group is not None
    assert group.title == "Rename me"
    assert group.text == ""
    assert group.position == (0.0, 0.0)
    view.close()
