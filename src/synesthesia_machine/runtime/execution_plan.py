"""Immutable output of graph validation and deterministic compilation."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from synesthesia_machine.contracts.runtime_values import ParameterValue, PortType
from synesthesia_machine.nodes.base import NodeDefinition, ParameterUpdateMode


class ScalarConversion(StrEnum):
    INT_TO_FLOAT = "INT_TO_FLOAT"


@dataclass(frozen=True, slots=True, order=True)
class PortKey:
    node_id: UUID
    port_id: str


@dataclass(frozen=True, slots=True)
class InputBinding:
    source: PortKey
    conversion: ScalarConversion | None = None


def _empty_parameters() -> dict[str, ParameterValue]:
    return {}


def _empty_input_bindings() -> dict[str, InputBinding]:
    return {}


def _empty_port_types() -> dict[str, PortType]:
    return {}


@dataclass(frozen=True, slots=True)
class CompiledNode:
    node_id: UUID
    definition: NodeDefinition
    parameters: Mapping[str, ParameterValue] = field(default_factory=_empty_parameters)
    input_bindings: Mapping[str, InputBinding] = field(default_factory=_empty_input_bindings)
    input_types: Mapping[str, PortType] = field(default_factory=_empty_port_types)
    output_types: Mapping[str, PortType] = field(default_factory=_empty_port_types)
    clock_id: UUID | None = None
    is_static: bool = True
    is_demanded: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "input_bindings", MappingProxyType(dict(self.input_bindings)))
        object.__setattr__(self, "input_types", MappingProxyType(dict(self.input_types)))
        object.__setattr__(self, "output_types", MappingProxyType(dict(self.output_types)))

    @property
    def state_retention_key(self) -> tuple[object, ...]:
        """Describe when this node's runtime state is safe to retain across plans."""

        state_parameters = tuple(
            (parameter.id, self.parameters[parameter.id])
            for parameter in self.definition.parameters
            if parameter.update_mode is not ParameterUpdateMode.LIVE
        )
        return (
            self.definition.type_id,
            self.definition.implementation_version,
            self.clock_id,
            state_parameters,
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    document_id: UUID
    graph_revision: int
    nodes: tuple[CompiledNode, ...]
    demand_roots: frozenset[UUID]

    def node(self, node_id: UUID) -> CompiledNode | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    @property
    def topological_node_ids(self) -> tuple[UUID, ...]:
        return tuple(node.node_id for node in self.nodes)
