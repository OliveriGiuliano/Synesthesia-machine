"""Scene-level layout command tests: undo behaviour, overlap, preview clearance.

The pure layout algorithms (alignment, distribution, tidy) are tested
headless in ``tests/graph/test_layout.py``.
"""

from __future__ import annotations

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import AlignMode
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.ui.canvas import GraphScene
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME


def test_scene_arrangement_is_one_exact_undoable_move(qapp: QApplication) -> None:
    del qapp
    session = DocumentSession(create_utility_registry())
    first = session.add_node("synmachine.utility.number", (30.0, 80.0))
    second = session.add_node("synmachine.utility.number", (180.0, 10.0))
    third = session.add_node("synmachine.utility.number", (400.0, 150.0))
    scene = GraphScene(session, DEFAULT_THEME)
    scene.select_node_ids({first, second, third})
    before = session.document.snapshot().nodes
    stack_count = session.undo_stack.count()

    scene.align_selection(AlignMode.TOP)
    after = session.document.snapshot().nodes
    assert after != before
    assert session.undo_stack.count() == stack_count + 1
    session.undo_stack.undo()
    assert session.document.snapshot().nodes == before
    session.undo_stack.redo()
    assert session.document.snapshot().nodes == after


def test_organize_graph_includes_visualizers_and_prevents_node_overlap(
    qapp: QApplication,
) -> None:
    del qapp
    session = DocumentSession(create_application_registry())
    source = session.add_node("synmachine.utility.number", (20.0, 20.0))
    transform = session.add_node("synmachine.utility.pass_through", (20.0, 20.0))
    visualizer = session.add_node("synmachine.visualization.note_visualizer", (20.0, 20.0))
    session.add_connection(source, "value", transform, "value")
    scene = GraphScene(session, DEFAULT_THEME)
    before = session.document.snapshot().nodes
    stack_count = session.undo_stack.count()

    scene.organize_graph()

    assert session.undo_stack.count() == stack_count + 1
    assert session.document.node(visualizer) is not None
    rectangles = tuple(item.body_scene_rect for item in scene.node_items.values())
    assert all(
        not first.intersects(second)
        for index, first in enumerate(rectangles)
        for second in rectangles[index + 1 :]
    )
    session.undo_stack.undo()
    assert session.document.snapshot().nodes == before


def test_organize_graph_reserves_clearance_for_video_preview_pills(
    qapp: QApplication,
) -> None:
    del qapp
    session = DocumentSession(create_application_registry())
    source = session.add_node("synmachine.input.load_video", (20.0, 20.0))
    visualizer = session.add_node("synmachine.visualization.display_image_data", (20.0, 20.0))
    connection = session.add_connection(source, "image", visualizer, "image")
    scene = GraphScene(session, DEFAULT_THEME)
    preview = QImage(1920, 1080, QImage.Format.Format_RGB32)
    scene.set_connection_image_preview(source, "image", preview)

    scene.organize_graph()

    preview_rect = scene.connection_items[connection].preview_scene_rect
    assert not preview_rect.isEmpty()
    preview_with_safety_margin = preview_rect.adjusted(-10.0, -10.0, 10.0, 10.0)
    assert all(
        not preview_with_safety_margin.intersects(item.body_scene_rect)
        for item in scene.node_items.values()
    )
