"""Built-in scalar utility and channel bridge nodes."""

from synesthesia_machine.nodes.utility.core import create_utility_registry
from synesthesia_machine.nodes.utility.scalar_bridges import create_scalar_bridge_definitions

__all__ = ["create_scalar_bridge_definitions", "create_utility_registry"]
