"""Application node-registry composition without UI or runtime side effects."""

from synesthesia_machine.nodes.image import create_image_definitions
from synesthesia_machine.nodes.input import create_input_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.nodes.synesthesia import create_synesthesia_definitions
from synesthesia_machine.nodes.utility import create_utility_registry


def create_application_registry() -> NodeRegistry:
    """Compose all implemented definitions and enforce unique stable type IDs."""

    utility = create_utility_registry()
    return NodeRegistry(
        (
            *utility.definitions(),
            *create_input_definitions(),
            *create_image_definitions(),
            *create_synesthesia_definitions(),
        )
    )


__all__ = ["create_application_registry"]
