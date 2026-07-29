"""Deterministic registry for immutable node definitions."""

from collections.abc import Iterable, Iterator

from synesthesia_machine.nodes.base import NodeDefinition


class NodeRegistry:
    def __init__(self, definitions: Iterable[NodeDefinition] = ()) -> None:
        self._definitions: dict[str, NodeDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: NodeDefinition) -> None:
        if definition.type_id in self._definitions:
            msg = f"Node type already registered: {definition.type_id}"
            raise ValueError(msg)
        self._definitions[definition.type_id] = definition

    def get(self, type_id: str) -> NodeDefinition | None:
        return self._definitions.get(type_id)

    def require(self, type_id: str) -> NodeDefinition:
        definition = self.get(type_id)
        if definition is None:
            msg = f"Unknown node type: {type_id}"
            raise KeyError(msg)
        return definition

    def definitions(self) -> tuple[NodeDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def __iter__(self) -> Iterator[NodeDefinition]:
        return iter(self.definitions())

    def __len__(self) -> int:
        return len(self._definitions)
