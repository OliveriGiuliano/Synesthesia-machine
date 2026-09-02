"""Graph area size and node/group bounds enforcement tests.

The graph area must be large enough that laying out graphs rarely hits an edge,
and nodes or groups must never be left outside it by interactive or committed
movement. Drags are simulated by delivering the scene mouse events Qt would
deliver to the dragged item, which exercises the item handlers and the scene
commit paths that enforce the bounds.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPointF, QSignalBlocker, Qt
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsSceneMouseEvent

from synesthesia_machine.graph import GroupKind
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.ui.canvas import (
    SCENE_EXTENT_X,
    SCENE_EXTENT_Y,
    GraphScene,
)
from synesthesia_machine.ui.commands import MoveNodesCommand
from synesthesia_machine.ui.graphics import GroupGraphicsItem, NodeGraphicsItem
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME


@pytest.fixture
def canvas(qapp: QApplication) -> Iterator[GraphScene]:
    del qapp
    scene = GraphScene(DocumentSession(create_utility_registry()), DEFAULT_THEME)
    yield scene
    blocker = QSignalBlocker(scene)
    scene.clear()
    del blocker
    scene.session.undo_stack.clear()
    scene.session.deleteLater()
    scene.deleteLater()


def _scene_mouse_event(
    event_type: QGraphicsSceneMouseEvent.Type,
    scene_point: QPointF,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QGraphicsSceneMouseEvent:
    event = QGraphicsSceneMouseEvent(event_type)
    event.setPos(scene_point)
    event.setScenePos(scene_point)
    event.setButton(button)
    event.setButtons(buttons)
    event.setModifiers(Qt.KeyboardModifier.NoModifier)
    return event


def _drag_item(item: QGraphicsItem, target_scene: QPointF, inset: float = 15.0) -> None:
    """Press the item near its top-left corner, drag to *target_scene*, release."""

    press_point = item.pos() + QPointF(inset, inset)
    item.mousePressEvent(
        _scene_mouse_event(
            QGraphicsSceneMouseEvent.Type.MouseButtonPress,
            press_point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )
    item.mouseMoveEvent(
        _scene_mouse_event(
            QGraphicsSceneMouseEvent.Type.MouseMove,
            target_scene,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
        )
    )
    item.mouseReleaseEvent(
        _scene_mouse_event(
            QGraphicsSceneMouseEvent.Type.MouseButtonRelease,
            target_scene,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
    )


def _assert_item_inside_scene(scene: GraphScene, item: QGraphicsItem) -> None:
    rect = scene.sceneRect()
    bounds = item.boundingRect()
    x, y = item.pos().x(), item.pos().y()
    assert x >= rect.left() - 1e-6
    assert y >= rect.top() - 1e-6
    assert x + bounds.width() <= rect.right() + 1e-6
    assert y + bounds.height() <= rect.bottom() + 1e-6


def test_graph_area_is_far_larger_than_legacy_bounds(canvas: GraphScene) -> None:
    rect = canvas.sceneRect()
    # Legacy bounds were +/-4000 x +/-3000; the new area is five times wider
    # and taller on each axis so large graphs can be arranged freely.
    assert rect.left() == pytest.approx(-SCENE_EXTENT_X)
    assert rect.right() == pytest.approx(SCENE_EXTENT_X)
    assert rect.top() == pytest.approx(-SCENE_EXTENT_Y)
    assert rect.bottom() == pytest.approx(SCENE_EXTENT_Y)
    assert rect.width() >= 4.0 * 8000.0
    assert rect.height() >= 4.0 * 6000.0
    assert rect.contains(QPointF(0.0, 0.0))


def test_node_dragged_past_the_edge_stays_inside_the_graph_area(canvas: GraphScene) -> None:
    node_id = canvas.session.add_node("synmachine.utility.number", (SCENE_EXTENT_X - 500.0, 0.0))
    item = canvas.node_items[node_id]
    width = item.boundingRect().width()

    _drag_item(item, QPointF(10.0 * SCENE_EXTENT_X, 0.0), inset=0.0)

    # The node's right edge is pinned to the scene's right edge; it never crosses it.
    assert item.pos().x() == pytest.approx(SCENE_EXTENT_X - width)
    _assert_item_inside_scene(canvas, item)
    node = canvas.session.document.node(node_id)
    assert node is not None
    assert node.position[0] == pytest.approx(SCENE_EXTENT_X - width)
    assert node.position[1] == pytest.approx(0.0)


def test_selection_drag_moves_as_a_unit_and_stops_at_the_edge(canvas: GraphScene) -> None:
    right_id = canvas.session.add_node("synmachine.utility.number", (SCENE_EXTENT_X - 500.0, 0.0))
    left_id = canvas.session.add_node("synmachine.utility.number", (SCENE_EXTENT_X - 1500.0, 0.0))
    right = canvas.node_items[right_id]
    left = canvas.node_items[left_id]
    width = right.boundingRect().width()
    original_offset = left.pos().x() - right.pos().x()

    canvas.select_node_ids({right_id, left_id})
    _drag_item(right, QPointF(10.0 * SCENE_EXTENT_X, 0.0))

    # The whole selection stopped as one unit at the edge: the right node's edge
    # touches the scene boundary and the relative layout is preserved.
    assert right.pos().x() == pytest.approx(SCENE_EXTENT_X - width)
    assert left.pos().x() == pytest.approx(SCENE_EXTENT_X - width + original_offset)
    _assert_item_inside_scene(canvas, right)
    _assert_item_inside_scene(canvas, left)
    for node_id in (right_id, left_id):
        node = canvas.session.document.node(node_id)
        assert node is not None
        assert node.position[0] + width <= canvas.sceneRect().right() + 1e-6

    # One undo step restores the pre-drag layout for both nodes.
    canvas.session.undo_stack.undo()
    assert canvas.session.document.node(right_id).position == (SCENE_EXTENT_X - 500.0, 0.0)  # type: ignore[union-attr]
    assert canvas.session.document.node(left_id).position == (SCENE_EXTENT_X - 1500.0, 0.0)  # type: ignore[union-attr]


def test_dragging_a_free_node_stays_inside_when_aimed_past_top_left_edge(
    canvas: GraphScene,
) -> None:
    node_id = canvas.session.add_node(
        "synmachine.utility.number", (-SCENE_EXTENT_X + 500.0, -SCENE_EXTENT_Y + 500.0)
    )
    item = canvas.node_items[node_id]

    _drag_item(item, QPointF(-10.0 * SCENE_EXTENT_X, -10.0 * SCENE_EXTENT_Y))

    _assert_item_inside_scene(canvas, item)
    assert item.pos().x() == pytest.approx(canvas.sceneRect().left())
    assert item.pos().y() == pytest.approx(canvas.sceneRect().top())


def test_commit_node_move_clamps_out_of_bounds_positions(canvas: GraphScene) -> None:
    node_id = canvas.session.add_node("synmachine.utility.number", (100.0, 100.0))
    item = canvas.node_items[node_id]
    width = item.boundingRect().width()
    height = item.boundingRect().height()

    # A position change that bypasses the drag handlers must be corrected both
    # live (item) and on commit (document).
    item.setPos(2.5 * SCENE_EXTENT_X, 2.5 * SCENE_EXTENT_Y)
    _assert_item_inside_scene(canvas, item)
    canvas.commit_node_move({node_id: (100.0, 100.0)})

    node = canvas.session.document.node(node_id)
    assert node is not None
    assert node.position[0] == pytest.approx(SCENE_EXTENT_X - width)
    assert node.position[1] == pytest.approx(SCENE_EXTENT_Y - height)


def test_programmatic_out_of_bounds_move_is_pulled_back_into_the_scene(canvas: GraphScene) -> None:
    node_id = canvas.session.add_node("synmachine.utility.number", (0.0, 0.0))
    # Simulate a position that entered the document without scene mediation
    # (legacy data or an out-of-band write): the scene must not strand the node.
    canvas.session.push(
        MoveNodesCommand(
            canvas.session.document,
            {node_id: (0.0, 0.0)},
            {node_id: (10.0 * SCENE_EXTENT_X, 10.0 * SCENE_EXTENT_Y)},
        )
    )
    item = canvas.node_items[node_id]
    assert isinstance(item, NodeGraphicsItem)
    _assert_item_inside_scene(canvas, item)


def test_group_moved_past_the_edge_is_kept_inside_the_graph_area(canvas: GraphScene) -> None:
    group_id = canvas.session.add_group(
        GroupKind.GROUP,
        (SCENE_EXTENT_X - 500.0, 0.0),
        title="Edge group",
        size=(480.0, 320.0),
    )
    group = canvas.group_items[group_id]
    assert isinstance(group, GroupGraphicsItem)

    # Group drags are bounded by the item handler; the commit clamps the document.
    _drag_item(group, QPointF(10.0 * SCENE_EXTENT_X, 0.0), inset=16.0)
    _assert_item_inside_scene(canvas, group)

    rect = canvas.sceneRect()
    assert group.pos().x() == pytest.approx(rect.right() - group.boundingRect().width())
    group_model = canvas.session.document.group(group_id)
    assert group_model is not None
    assert group_model.position[0] == pytest.approx(rect.right() - group.boundingRect().width())
    assert group_model.position[0] >= rect.left() - 1e-6
    # The drag target sat 16 scene units above the press point, so the group
    # moved up by exactly the inset and no further (y stayed inside the area).
    assert group_model.position[1] == pytest.approx(-16.0)


def test_new_node_anchor_is_clamped_to_the_graph_area(canvas: GraphScene) -> None:
    clamped = canvas.clamp_new_node_anchor(QPointF(10.0 * SCENE_EXTENT_X, -10.0 * SCENE_EXTENT_Y))
    rect = canvas.sceneRect()
    assert rect.left() <= clamped[0] <= rect.right()
    assert rect.top() <= clamped[1] <= rect.bottom()
    assert clamped[0] == pytest.approx(rect.right() - DEFAULT_THEME.metrics.node_width)
    assert clamped[1] == pytest.approx(rect.top())
