"""Profiler controls, sorting, copy report, and canvas heatmap tests."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtWidgets import QApplication

from synesthesia_machine.contracts import NodeProfile
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.ui.canvas import GraphScene
from synesthesia_machine.ui.profiler import ProfilerPanel, profile_heat_levels
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME

NODE_A = UUID("82000000-0000-0000-0000-000000000001")
NODE_B = UUID("82000000-0000-0000-0000-000000000002")


def _profile(node_id: UUID, p95_ms: float, *, calls: int = 10) -> NodeProfile:
    return NodeProfile(
        node_id=node_id,
        invocation_count=calls,
        error_count=1 if node_id == NODE_B else 0,
        window_size=min(calls, 4),
        last_duration_ms=p95_ms / 2,
        ema_duration_ms=p95_ms / 2,
        p50_duration_ms=p95_ms / 2,
        p95_duration_ms=p95_ms,
        max_duration_ms=p95_ms * 1.1,
        output_summary="value=FLOAT",
        output_bytes=8,
    )


def test_profiler_table_freeze_reset_copy_and_numeric_sort(qapp: QApplication) -> None:
    panel = ProfilerPanel()
    reset_count = 0

    def record_reset() -> None:
        nonlocal reset_count
        reset_count += 1

    panel.resetRequested.connect(record_reset)
    profiles = (_profile(NODE_A, 10.0, calls=2), _profile(NODE_B, 2.0, calls=11))
    assert panel.set_profiles(profiles, {NODE_A: "Slow", NODE_B: "Fast"})
    assert panel.table.rowCount() == 2

    panel.table.sortItems(6)
    assert panel.table.item(0, 0).text() == "Slow"
    panel.copy_report()
    assert "Slow\t5.000" in qapp.clipboard().text()
    assert "p95 ms" in qapp.clipboard().text()

    panel.freeze_button.setChecked(True)
    assert not panel.set_profiles((_profile(NODE_B, 1.0),))
    assert panel.table.rowCount() == 2
    panel.reset_button.click()
    assert reset_count == 1
    assert panel.table.rowCount() == 0


def test_profile_heat_levels_are_frame_budget_based_and_render_on_nodes(
    qapp: QApplication,
) -> None:
    del qapp
    session = DocumentSession(create_utility_registry())
    node_id = session.add_node("synmachine.utility.number", (0.0, 0.0))
    scene = GraphScene(session, DEFAULT_THEME)
    levels = profile_heat_levels((_profile(node_id, 1000.0 / 60.0),))

    scene.set_node_heatmap(levels)
    assert scene.node_items[node_id].heat_level == 1.0

    scene.set_node_heatmap(None)
    assert scene.node_items[node_id].heat_level is None
