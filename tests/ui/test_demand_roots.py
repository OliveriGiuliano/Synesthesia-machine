"""Headless tests for the Qt-free demand-root policy.

These exercise ``compute_demand_roots`` directly — no main window, no event
loop, no engine — so the ADR-0008/0011 demand policy is covered without the
offscreen-window and timing machinery the transport tests use.
"""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.graph import GraphCompiler, GraphDocument, GraphSnapshot
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.nodes.composition import create_builtin_registry
from synesthesia_machine.ui.connection_state import PREVIEW_VISIBLE_KEY
from synesthesia_machine.ui.demand_roots import compute_demand_roots
from synesthesia_machine.ui.view_models import project_graph


def _snapshot_and_types(
    document: GraphDocument, registry: NodeRegistry
) -> tuple[GraphSnapshot, dict[UUID, str]]:
    """Snapshot the graph and return each connection's projected preview type."""

    snapshot = document.snapshot()
    compilation = GraphCompiler(registry).compile(snapshot)
    view_model = project_graph(snapshot, registry, compilation.report, compilation=compilation)
    types = {c.connection_id: c.type_name for c in view_model.connections}
    return snapshot, types


def test_visualizer_docks_gate_demand_roots() -> None:
    registry = create_builtin_registry()
    document = GraphDocument()
    image_id = document.add_node("synmachine.visualization.display_image_data")
    note_id = document.add_node("synmachine.visualization.note_visualizer")
    snapshot, types = _snapshot_and_types(document, registry)

    assert set(
        compute_demand_roots(
            snapshot, registry, types, image_dock_visible=True, note_dock_visible=True
        )
    ) == {image_id, note_id}
    assert set(
        compute_demand_roots(
            snapshot, registry, types, image_dock_visible=True, note_dock_visible=False
        )
    ) == {image_id}
    assert set(
        compute_demand_roots(
            snapshot, registry, types, image_dock_visible=False, note_dock_visible=True
        )
    ) == {note_id}
    assert (
        compute_demand_roots(
            snapshot, registry, types, image_dock_visible=False, note_dock_visible=False
        )
        == ()
    )


def test_image_link_pill_keeps_producer_when_dock_hidden() -> None:
    registry = create_builtin_registry()
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video", parameters={"file_path": "unused.mp4"}
    )
    display_id = document.add_node("synmachine.visualization.display_image_data")
    connection_id = document.add_connection(source_id, "image", display_id, "image")

    # Dock visible: the display node is a root regardless of the pill.
    snapshot, types = _snapshot_and_types(document, registry)
    assert display_id in compute_demand_roots(
        snapshot, registry, types, image_dock_visible=True, note_dock_visible=False
    )

    # Dock hidden but the pill is visible (the default): the display node is no
    # longer a root, but the pill keeps its producer (the source) demanded.
    roots = compute_demand_roots(
        snapshot, registry, types, image_dock_visible=False, note_dock_visible=False
    )
    assert display_id not in roots
    assert source_id in roots

    # Hiding the link pill drops the producer root; nothing in the chain is demanded.
    document.set_connection_ui_state(connection_id, PREVIEW_VISIBLE_KEY, False)
    snapshot, types = _snapshot_and_types(document, registry)
    roots = compute_demand_roots(
        snapshot, registry, types, image_dock_visible=False, note_dock_visible=False
    )
    assert display_id not in roots
    assert source_id not in roots

    # Showing the dock demands the display node again even with the pill hidden.
    assert display_id in compute_demand_roots(
        snapshot, registry, types, image_dock_visible=True, note_dock_visible=False
    )


def test_scalar_link_pill_keeps_producer_without_a_display_root() -> None:
    registry = create_builtin_registry()
    document = GraphDocument()
    number_id = document.add_node("synmachine.utility.number")
    math_id = document.add_node("synmachine.utility.math")
    connection_id = document.add_connection(number_id, "value", math_id, "a")

    # Both nodes are stateless (no sink/visualizer root), so the scalar producer
    # is demanded only because its link pill is visible (the default).
    snapshot, types = _snapshot_and_types(document, registry)
    roots = compute_demand_roots(
        snapshot, registry, types, image_dock_visible=False, note_dock_visible=False
    )
    assert number_id in roots
    assert math_id not in roots

    # Hiding the link pill drops the scalar producer; nothing in the chain is demanded.
    document.set_connection_ui_state(connection_id, PREVIEW_VISIBLE_KEY, False)
    snapshot, types = _snapshot_and_types(document, registry)
    roots = compute_demand_roots(
        snapshot, registry, types, image_dock_visible=False, note_dock_visible=False
    )
    assert number_id not in roots
    assert math_id not in roots
