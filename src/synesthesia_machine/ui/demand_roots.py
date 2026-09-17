"""Qt-free demand-root policy (ADR-0008/0011).

Which nodes the engine must execute is decided here, from the graph snapshot,
the node registry, the per-connection preview types, and which preview docks
are visible. The function is pure and Qt-free, so the policy is testable without
a main window or the engine; the editor feeds it the connection preview types
projected from the current graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.graph import GraphSnapshot
from synesthesia_machine.nodes import ExecutionKind, NodeRegistry
from synesthesia_machine.ui.connection_state import PREVIEW_VISIBLE_KEY
from synesthesia_machine.ui.preview_families import (
    pill_type_names,
    visualizer_type_ids,
)

NOTE_VISUALIZER_TYPE_IDS = visualizer_type_ids("note")


def compute_demand_roots(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    connection_preview_types: Mapping[UUID, str],
    *,
    image_dock_visible: bool,
    note_dock_visible: bool,
) -> tuple[UUID, ...]:
    """Return the demand-root node ids the engine should execute.

    Sinks are always roots. A note visualizer is a root while the note dock is
    visible; any other (image/channel display) visualizer is a root while the
    image dock is visible. A visible value or image pill adds its producer so
    the link can show live data even when no display node demands the branch.
    """
    roots: set[UUID] = set()
    pill_types = pill_type_names()
    for node in snapshot.nodes:
        kind = registry.require(node.type_id).execution_kind
        if kind is ExecutionKind.SINK:
            roots.add(node.id)
        elif kind is ExecutionKind.VISUALIZER:
            # Note visualizers feed their own dock. Image/channel display nodes
            # remain the demand anchors for the preview dock; link pills are
            # anchored on producers instead (see below).
            if node.type_id in NOTE_VISUALIZER_TYPE_IDS:
                if note_dock_visible:
                    roots.add(node.id)
            elif image_dock_visible:
                roots.add(node.id)
    for connection in snapshot.connections:
        if not bool(connection.ui_state.get(PREVIEW_VISIBLE_KEY, True)):
            continue
        if connection_preview_types.get(connection.id) in pill_types:
            roots.add(connection.source_node_id)
    return tuple(sorted(roots, key=str))
