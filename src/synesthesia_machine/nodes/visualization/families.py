"""Headless preview-dock policy for display visualizers.

Display visualizer node definitions declare the preview dock they feed
(``NodeDefinition.presentation.preview_dock``); both image and channel displays take the
image dock's slot. The editor enforces one display visualizer per dock:
adding one removes the dock's incumbent from the document. The demand
policy reads the same declaration, so the session, the add-node command,
and the demand policy all consult this metadata instead of re-declaring it.
"""

from __future__ import annotations

from synesthesia_machine.nodes.base import PreviewDock
from synesthesia_machine.nodes.registry import NodeRegistry


def visualizer_dock(registry: NodeRegistry, type_id: str) -> PreviewDock | None:
    """Preview dock a display visualizer type feeds, if it is one."""

    definition = registry.get(type_id)
    return None if definition is None else definition.presentation.preview_dock


def display_visualizer_type_ids(registry: NodeRegistry, dock: PreviewDock) -> frozenset[str]:
    """Display-visualizer type ids that occupy one preview dock's slot."""

    return frozenset(
        definition.execution.type_id
        for definition in registry.definitions()
        if definition.presentation.preview_dock is dock
    )


def visualizer_replacement_type_ids(registry: NodeRegistry, type_id: str) -> frozenset[str]:
    """Existing display-visualizer type ids a new node of ``type_id`` replaces.

    Adding a display visualizer removes the other members of its dock (an
    image and a channel display share the image dock); adding anything else
    replaces nothing.
    """

    dock = visualizer_dock(registry, type_id)
    if dock is None:
        return frozenset()
    return display_visualizer_type_ids(registry, dock)
