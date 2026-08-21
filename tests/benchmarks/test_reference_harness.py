"""Deterministic reference graph and benchmark harness contracts."""

from __future__ import annotations

from pathlib import Path

from tools.reference_benchmark import (
    BLUR_ID,
    CHANNEL_DISPLAY_ID,
    NOTE_VISUALIZER_ID,
    SOURCE_ID,
    create_reference_document,
    write_reference_graph,
)

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphCompiler


def test_reference_graph_is_deterministic_valid_and_demands_both_preview_branches(
    tmp_path: Path,
) -> None:
    document = create_reference_document(str(tmp_path / "synthetic.mp4"))
    snapshot = document.snapshot()
    result = GraphCompiler(create_application_registry()).compile(snapshot)

    assert not result.report.errors
    assert result.plan is not None
    assert len(snapshot.nodes) == 9
    assert len(snapshot.connections) == 8
    demanded_ids = {node.node_id for node in result.plan.nodes if node.is_demanded}
    assert demanded_ids == {node.id for node in snapshot.nodes}
    assert document.node(SOURCE_ID) is not None
    blur = document.node(BLUR_ID)
    assert blur is not None
    assert blur.parameters["kernel_width"] == 5
    assert blur.parameters["kernel_height"] == 5
    assert {NOTE_VISUALIZER_ID, CHANNEL_DISPLAY_ID}.issubset(demanded_ids)


def test_checked_reference_serializer_is_reproducible(tmp_path: Path) -> None:
    path = tmp_path / "reference.synmachine.json"

    write_reference_graph(path)
    first = path.read_bytes()
    write_reference_graph(path)

    assert path.read_bytes() == first
    assert b"synthetic-500x500-60fps.mp4" in first
