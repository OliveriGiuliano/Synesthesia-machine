"""Profile large-graph canvas construction, editing, lazy widgets, and low-detail mode."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import save_graph
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME


@dataclass(frozen=True, slots=True)
class LargeGraphReport:
    node_count: int
    scene_build_ms: float
    incremental_edit_ms: float
    large_graph_mode: bool
    initial_parameter_editor_count: int
    selected_parameter_editor_count: int
    low_detail_visible_child_count: int
    unaffected_item_identity_preserved: bool
    passed: bool
    criteria: tuple[str, ...]


def profile_large_graph(node_count: int = 500) -> LargeGraphReport:
    if node_count < 200:
        raise ValueError("Large-graph profiling requires at least 200 nodes")
    application = QApplication.instance()
    if application is None:
        application = QApplication(["synmachine-phase7-large-graph"])
    qapp = cast(QApplication, application)
    registry = create_utility_registry()
    document = GraphDocument()
    node_ids = tuple(
        document.add_node(
            "synmachine.utility.number",
            parameters={"number_type": "FLOAT", "float_value": float(index)},
            position=((index % 25) * 285.0, (index // 25) * 155.0),
        )
        for index in range(node_count)
    )
    with tempfile.TemporaryDirectory(prefix="synmachine-phase7-large-graph-") as temporary:
        graph_path = Path(temporary) / "large.synmachine.json"
        save_graph(graph_path, document.snapshot())
        session = DocumentSession(registry)
        session.open_document(graph_path)
        started = time.perf_counter_ns()
        scene = GraphScene(session, DEFAULT_THEME)
        view = GraphView(scene, DEFAULT_THEME)
        view.resize(1280, 720)
        qapp.processEvents()
        scene_build_ms = (time.perf_counter_ns() - started) / 1_000_000.0

        initial_editor_count = sum(
            len(item.parameter_editors) for item in scene.node_items.values()
        )
        large_graph_mode = scene.large_graph_mode
        unaffected_id = node_ids[-1]
        unaffected_item = scene.node_items[unaffected_id]
        started = time.perf_counter_ns()
        session.set_parameter(node_ids[0], "float_value", -1.0)
        qapp.processEvents()
        incremental_edit_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        identity_preserved = scene.node_items[unaffected_id] is unaffected_item

        scene.select_node_ids({node_ids[0]})
        qapp.processEvents()
        selected_editor_count = len(scene.node_items[node_ids[0]].parameter_editors)
        scene.set_detail_level(0.3)
        low_detail_visible_children = sum(
            child.isVisible()
            for item in scene.node_items.values()
            for child in (*item.ports.values(), *item.parameter_editors.values())
        )
        criteria = (
            "scene build <= 5000 ms",
            "single parameter edit <= 1000 ms",
            "no bulk parameter editors before selection",
            "unaffected graphics item identity preserved",
            "ports and editors hidden below 0.55 zoom",
        )
        passed = (
            scene_build_ms <= 5000.0
            and incremental_edit_ms <= 1000.0
            and initial_editor_count == 0
            and selected_editor_count > 0
            and identity_preserved
            and low_detail_visible_children == 0
        )
        view.close()
        scene.deleteLater()
        qapp.processEvents()
    return LargeGraphReport(
        node_count,
        round(scene_build_ms, 3),
        round(incremental_edit_ms, 3),
        large_graph_mode,
        initial_editor_count,
        selected_editor_count,
        low_detail_visible_children,
        identity_preserved,
        passed,
        criteria,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-count", type=int, default=500)
    parser.add_argument("--output", type=Path, default=Path("docs/phase-7-large-graph.json"))
    args = parser.parse_args()
    report = profile_large_graph(args.node_count)
    payload = json.dumps(asdict(report), indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
