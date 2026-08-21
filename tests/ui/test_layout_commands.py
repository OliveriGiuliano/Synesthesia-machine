"""Deterministic alignment, distribution, and tidy-selection tests."""

from __future__ import annotations

from itertools import pairwise
from uuid import UUID

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import (
    AlignMode,
    ConnectionModel,
    DistributionAxis,
    LayoutBox,
    align_boxes,
    distribute_boxes,
    tidy_boxes,
)
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.ui.canvas import GraphScene
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME

NODE_A = UUID("70000000-0000-0000-0000-000000000011")
NODE_B = UUID("70000000-0000-0000-0000-000000000012")
NODE_C = UUID("70000000-0000-0000-0000-000000000013")
NODE_D = UUID("70000000-0000-0000-0000-000000000014")


def _boxes() -> tuple[LayoutBox, ...]:
    return (
        LayoutBox(NODE_A, 0.0, 20.0, 10.0, 20.0),
        LayoutBox(NODE_B, 30.0, 5.0, 10.0, 30.0),
        LayoutBox(NODE_C, 100.0, 40.0, 20.0, 10.0),
    )


def test_all_alignment_modes_use_visual_extents_deterministically() -> None:
    boxes = _boxes()
    assert align_boxes(boxes, AlignMode.LEFT) == {
        NODE_A: (0.0, 20.0),
        NODE_B: (0.0, 5.0),
        NODE_C: (0.0, 40.0),
    }
    assert align_boxes(boxes, AlignMode.RIGHT) == {
        NODE_A: (110.0, 20.0),
        NODE_B: (110.0, 5.0),
        NODE_C: (100.0, 40.0),
    }
    assert align_boxes(boxes, AlignMode.TOP) == {
        NODE_A: (0.0, 5.0),
        NODE_B: (30.0, 5.0),
        NODE_C: (100.0, 5.0),
    }
    assert align_boxes(boxes, AlignMode.BOTTOM) == {
        NODE_A: (0.0, 30.0),
        NODE_B: (30.0, 20.0),
        NODE_C: (100.0, 40.0),
    }
    horizontal = align_boxes(boxes, AlignMode.HORIZONTAL_CENTER)
    assert len({x + box.width / 2.0 for box in boxes for x, _ in [horizontal[box.node_id]]}) == 1
    vertical = align_boxes(boxes, AlignMode.VERTICAL_CENTER)
    assert len({y + box.height / 2.0 for box in boxes for _, y in [vertical[box.node_id]]}) == 1


def test_distribution_preserves_outer_extents_and_uses_equal_gaps() -> None:
    horizontal = distribute_boxes(_boxes(), DistributionAxis.HORIZONTAL)
    assert horizontal == {
        NODE_A: (0.0, 20.0),
        NODE_B: (50.0, 5.0),
        NODE_C: (100.0, 40.0),
    }

    vertical = distribute_boxes(_boxes(), DistributionAxis.VERTICAL)
    ordered = sorted(_boxes(), key=lambda box: box.y)
    gaps = []
    for first, second in pairwise(ordered):
        first_y = vertical[first.node_id][1]
        second_y = vertical[second.node_id][1]
        gaps.append(second_y - (first_y + first.height))
    assert gaps[0] == gaps[1]


def test_tidy_uses_longest_path_layers_and_stable_vertical_order() -> None:
    boxes = (
        LayoutBox(NODE_A, 20.0, 80.0, 100.0, 40.0),
        LayoutBox(NODE_B, 10.0, 10.0, 120.0, 50.0),
        LayoutBox(NODE_C, 300.0, 30.0, 90.0, 60.0),
        LayoutBox(NODE_D, 500.0, 20.0, 110.0, 45.0),
    )
    connections = (
        ConnectionModel(UUID(int=1), NODE_A, "out", NODE_C, "in_a"),
        ConnectionModel(UUID(int=2), NODE_B, "out", NODE_C, "in_b"),
        ConnectionModel(UUID(int=3), NODE_C, "out", NODE_D, "in"),
    )

    positions = tidy_boxes(boxes, connections, horizontal_gap=80.0, vertical_gap=20.0)
    assert positions[NODE_A][0] == positions[NODE_B][0] == 10.0
    assert positions[NODE_B][1] < positions[NODE_A][1]
    assert positions[NODE_C][0] == 210.0
    assert positions[NODE_D][0] == 410.0


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


def test_layout_rejects_insufficient_selection_and_cycles() -> None:
    one = (LayoutBox(NODE_A, 0.0, 0.0, 10.0, 10.0),)
    with pytest.raises(ValueError, match="at least two"):
        align_boxes(one, AlignMode.LEFT)
    with pytest.raises(ValueError, match="at least three"):
        distribute_boxes(one, DistributionAxis.HORIZONTAL)

    two = (*one, LayoutBox(NODE_B, 20.0, 0.0, 10.0, 10.0))
    cycle = (
        ConnectionModel(UUID(int=10), NODE_A, "out", NODE_B, "in"),
        ConnectionModel(UUID(int=11), NODE_B, "out", NODE_A, "in"),
    )
    with pytest.raises(ValueError, match="acyclic"):
        tidy_boxes(two, cycle)
