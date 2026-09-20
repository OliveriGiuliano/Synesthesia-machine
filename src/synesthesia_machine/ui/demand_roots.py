"""Qt-free demand-root policy (ADR-0008/0011).

Which nodes the engine must execute is decided here, from the published graph
projection. The function is pure and Qt-free, so the policy is testable without
a main window or the engine; the editor feeds it the view model it already
projects for rendering, and the projection owns the preview-visibility and
execution-kind interpretations, so this module never re-reads raw ui_state or
consult the registry. The display-visualizer families are headless node
metadata (``NodeDefinition.preview_family``), not a local type-id table.
"""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.ui.view_models import GraphViewModel


def compute_demand_roots(
    view: GraphViewModel,
    *,
    image_dock_visible: bool,
    note_dock_visible: bool,
) -> tuple[UUID, ...]:
    """Return the demand-root node ids the engine should execute.

    The node-side facts come from the projection's published derived slices
    (computed once per projection); this policy only joins them with the
    window's dock visibility and keeps the deterministic ordering.
    """
    roots: set[UUID] = set(view.derived.sink_node_ids)
    if note_dock_visible:
        # Note visualizers feed their own dock.
        roots.update(view.derived.note_visualizer_ids)
    if image_dock_visible:
        # Other display visualizers anchor the image preview dock.
        roots.update(view.derived.image_visualizer_ids)
    # A visible value or image pill adds its producer so the link can show
    # live data even when no display node demands the branch.
    roots.update(view.derived.pill_producer_ids)
    return tuple(sorted(roots, key=str))
