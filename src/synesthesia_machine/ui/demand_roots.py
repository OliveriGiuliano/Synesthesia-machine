"""Qt-free demand-root policy (ADR-0008/0011).

Which nodes the engine must execute is decided here, from the published graph
projection. The function is pure and Qt-free, so the policy is testable without
a main window or the engine; the editor feeds it the view model it already
projects for rendering, and the projection owns the preview-visibility and
execution-kind interpretations, so this module never re-reads raw ui_state or
consult the registry.
"""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.nodes import ExecutionKind
from synesthesia_machine.ui.preview_families import (
    pill_type_names,
    visualizer_type_ids,
)
from synesthesia_machine.ui.view_models import GraphViewModel

NOTE_VISUALIZER_TYPE_IDS = visualizer_type_ids("note")


def compute_demand_roots(
    view: GraphViewModel,
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
    for node in view.nodes:
        if node.execution_kind is ExecutionKind.SINK:
            roots.add(node.node_id)
        elif node.execution_kind is ExecutionKind.VISUALIZER:
            # Note visualizers feed their own dock. Image/channel display nodes
            # remain the demand anchors for the preview dock; link pills are
            # anchored on producers instead (see below).
            if node.type_id in NOTE_VISUALIZER_TYPE_IDS:
                if note_dock_visible:
                    roots.add(node.node_id)
            elif image_dock_visible:
                roots.add(node.node_id)
    for connection in view.connections:
        if not connection.preview_visible:
            continue
        if connection.type_name in pill_types:
            roots.add(connection.source_node_id)
    return tuple(sorted(roots, key=str))
