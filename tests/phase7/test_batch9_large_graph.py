"""Phase 7 batch 9 large-graph incremental rendering and profiling smoke tests."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication
from tools.phase7_large_graph import profile_large_graph

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import save_graph
from synesthesia_machine.ui.canvas import GraphScene
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME


def test_large_graph_profiler_meets_responsiveness_contract(qapp: QApplication) -> None:
    del qapp
    report = profile_large_graph(250)

    assert report.passed
    assert report.large_graph_mode
    assert report.initial_parameter_editor_count == 0
    assert report.selected_parameter_editor_count > 0
    assert report.low_detail_visible_child_count == 0
    assert report.unaffected_item_identity_preserved


def test_incremental_scene_sync_reuses_unchanged_items(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp
    registry = create_utility_registry()
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", position=(0.0, 0.0))
    second = document.add_node("synmachine.utility.number", position=(300.0, 0.0))
    path = tmp_path / "graph.synmachine.json"
    save_graph(path, document.snapshot())
    session = DocumentSession(registry)
    session.open_document(path)
    scene = GraphScene(session, DEFAULT_THEME)
    first_item = scene.node_items[first]
    second_item = scene.node_items[second]

    session.set_parameter(first, "float_value", 42.0)

    assert scene.node_items[first] is not first_item
    assert scene.node_items[second] is second_item
