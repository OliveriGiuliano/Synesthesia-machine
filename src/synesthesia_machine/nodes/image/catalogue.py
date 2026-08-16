"""Stable composition of the built-in image-node catalogue."""

from synesthesia_machine.nodes import NodeDefinition
from synesthesia_machine.nodes.image.adjustments import create_adjustment_definitions
from synesthesia_machine.nodes.image.channels import create_channel_definitions
from synesthesia_machine.nodes.image.dimensions import create_dimension_definitions
from synesthesia_machine.nodes.image.filters import create_filter_definitions
from synesthesia_machine.nodes.image.temporal import create_temporal_definitions
from synesthesia_machine.nodes.image.utilities import create_utility_definitions
from synesthesia_machine.nodes.utility.dynamic import create_difference_definitions


def create_image_definitions() -> tuple[NodeDefinition, ...]:
    """Return all image definitions in persistent display and registry order."""

    temporal = create_temporal_definitions()

    return (
        *create_dimension_definitions(),
        *create_adjustment_definitions(),
        *create_filter_definitions(),
        *create_difference_definitions(),
        *create_utility_definitions(),
        *create_channel_definitions(),
        *temporal[1:],
        *temporal[:1],
    )


__all__ = ["create_image_definitions"]
