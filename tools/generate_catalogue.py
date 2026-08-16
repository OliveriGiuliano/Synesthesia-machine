"""Regenerate the disconnected built-in-node catalogue example deterministically."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphSnapshot, NodeModel
from synesthesia_machine.persistence import graph_to_json

DOCUMENT_ID = UUID("65000000-0000-0000-0000-000000000000")


def generate_catalogue(path: str | Path = "examples/phase6/catalogue.synmachine.json") -> Path:
    registry = create_application_registry()
    nodes: list[NodeModel] = []
    for index, definition in enumerate(registry.definitions(), start=1):
        parameters, errors = definition.parameter_values({})
        if errors:
            raise RuntimeError(f"Invalid defaults for {definition.type_id}: {errors}")
        nodes.append(
            NodeModel(
                UUID(f"65000000-0000-0000-0000-{index:012d}"),
                definition.type_id,
                definition.implementation_version,
                parameters=parameters,
                position=((index - 1) % 5 * 320.0, (index - 1) // 5 * 260.0),
            )
        )
    snapshot = GraphSnapshot(DOCUMENT_ID, 0, tuple(nodes), ())
    destination = Path(path)
    destination.write_text(graph_to_json(snapshot), encoding="utf-8", newline="\n")
    return destination


if __name__ == "__main__":
    print(generate_catalogue())
