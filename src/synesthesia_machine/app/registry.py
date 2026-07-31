"""Application node-registry composition without UI or runtime side effects."""

from synesthesia_machine.midi import DebugSynth, DebugSynthFactory
from synesthesia_machine.nodes.image import create_image_definitions
from synesthesia_machine.nodes.input import create_input_definitions
from synesthesia_machine.nodes.output import create_output_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.nodes.synesthesia import create_synesthesia_definitions
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.nodes.visualization import create_visualization_definitions


def create_application_registry(*, synth_factory: DebugSynthFactory = DebugSynth) -> NodeRegistry:
    """Compose all implemented definitions and enforce unique stable type IDs."""

    utility = create_utility_registry()
    return NodeRegistry(
        (
            *utility.definitions(),
            *create_input_definitions(),
            *create_image_definitions(),
            *create_synesthesia_definitions(),
            *create_visualization_definitions(),
            *create_output_definitions(synth_factory=synth_factory),
        )
    )


__all__ = ["create_application_registry"]
