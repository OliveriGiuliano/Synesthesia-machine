"""Application-facing compatibility name for headless node composition."""

from synesthesia_machine.nodes.composition import create_builtin_registry

create_application_registry = create_builtin_registry


__all__ = ["create_application_registry"]
