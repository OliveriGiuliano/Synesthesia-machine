"""Headless tests for the preview-dock policy.

Display-visualizer dock membership is node-definition metadata
(``NodeDefinition.preview_dock``); the one-visualizer-per-dock replacement
rule the editor enforces must be testable here, without the UI.
"""

from __future__ import annotations

from synesthesia_machine.nodes import PreviewDock
from synesthesia_machine.nodes.composition import create_builtin_registry
from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
    display_visualizer_type_ids,
    visualizer_dock,
    visualizer_replacement_type_ids,
)


def test_display_visualizers_declare_their_docks() -> None:
    registry = create_builtin_registry()
    # Image and channel displays share the image dock's slot.
    assert visualizer_dock(registry, DISPLAY_IMAGE_DATA_TYPE_ID) is PreviewDock.IMAGE
    assert visualizer_dock(registry, CHANNEL_DISPLAY_TYPE_ID) is PreviewDock.IMAGE
    assert visualizer_dock(registry, NOTE_VISUALIZER_TYPE_ID) is PreviewDock.NOTE
    # Only display visualizers feed a dock.
    assert visualizer_dock(registry, "synmachine.utility.number") is None
    assert visualizer_dock(registry, "synmachine.input.load_video") is None
    assert visualizer_dock(registry, "synmachine.output.midi_out") is None


def test_docks_partition_the_display_visualizers() -> None:
    registry = create_builtin_registry()
    image = display_visualizer_type_ids(registry, PreviewDock.IMAGE)
    note = display_visualizer_type_ids(registry, PreviewDock.NOTE)
    assert image == frozenset({DISPLAY_IMAGE_DATA_TYPE_ID, CHANNEL_DISPLAY_TYPE_ID})
    assert note == frozenset({NOTE_VISUALIZER_TYPE_ID})
    # No display visualizer occupies two docks.
    assert not image & note


def test_adding_a_visualizer_replaces_its_whole_dock() -> None:
    registry = create_builtin_registry()
    # Both displays take the image dock's slot: adding either removes the
    # other, so a document never shows the image dock twice.
    assert visualizer_replacement_type_ids(registry, DISPLAY_IMAGE_DATA_TYPE_ID) == frozenset(
        {DISPLAY_IMAGE_DATA_TYPE_ID, CHANNEL_DISPLAY_TYPE_ID}
    )
    assert visualizer_replacement_type_ids(registry, CHANNEL_DISPLAY_TYPE_ID) == frozenset(
        {DISPLAY_IMAGE_DATA_TYPE_ID, CHANNEL_DISPLAY_TYPE_ID}
    )
    assert visualizer_replacement_type_ids(registry, NOTE_VISUALIZER_TYPE_ID) == frozenset(
        {NOTE_VISUALIZER_TYPE_ID}
    )


def test_adding_non_visualizers_replaces_nothing() -> None:
    registry = create_builtin_registry()
    assert visualizer_replacement_type_ids(registry, "synmachine.utility.number") == frozenset()
    assert visualizer_replacement_type_ids(registry, "synmachine.input.load_video") == frozenset()
    # Unknown type ids are safe: no dock, no replacement.
    assert visualizer_replacement_type_ids(registry, "synmachine.unknown.type") == frozenset()
