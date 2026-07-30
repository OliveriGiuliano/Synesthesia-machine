"""Immutable projections from graph-domain values to editor rendering data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.contracts import ParameterValue, PortType
from synesthesia_machine.graph import GraphSnapshot, LiteralValue, ValidationIssue, ValidationReport
from synesthesia_machine.nodes import NodeDefinition, NodeRegistry, ParameterSpec


@dataclass(frozen=True, slots=True)
class PortViewModel:
    node_id: UUID
    port_id: str
    label: str
    type_name: str
    is_output: bool
    is_parameter: bool = False
    connected: bool = False


@dataclass(frozen=True, slots=True)
class ParameterViewModel:
    spec: ParameterSpec
    value: LiteralValue
    connected: bool


@dataclass(frozen=True, slots=True)
class NodeViewModel:
    node_id: UUID
    type_id: str
    title: str
    category: str
    description: str
    position: tuple[float, float]
    inputs: tuple[PortViewModel, ...]
    outputs: tuple[PortViewModel, ...]
    parameters: tuple[ParameterViewModel, ...]
    issues: tuple[ValidationIssue, ...]
    collapsed: bool


@dataclass(frozen=True, slots=True)
class ConnectionViewModel:
    connection_id: UUID
    source_node_id: UUID
    source_port_id: str
    destination_node_id: UUID
    destination_port_id: str
    type_name: str
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class GraphViewModel:
    nodes: tuple[NodeViewModel, ...]
    connections: tuple[ConnectionViewModel, ...]


def project_graph(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    report: ValidationReport,
) -> GraphViewModel:
    incoming = {
        (connection.destination_node_id, connection.destination_port_id)
        for connection in snapshot.connections
    }
    node_issues: dict[UUID, list[ValidationIssue]] = {}
    connection_issues: dict[UUID, list[ValidationIssue]] = {}
    for issue in report.issues:
        if issue.node_id is not None:
            node_issues.setdefault(issue.node_id, []).append(issue)
        if issue.connection_id is not None:
            connection_issues.setdefault(issue.connection_id, []).append(issue)

    nodes: list[NodeViewModel] = []
    type_names: dict[tuple[UUID, str, bool], str] = {}
    for node in snapshot.nodes:
        definition = registry.require(node.type_id)
        parameters, _ = definition.parameter_values(node.parameters)
        inputs = tuple(
            PortViewModel(
                node.id,
                port.id,
                port.label,
                _type_name(definition, port.id, False, parameters),
                False,
                connected=(node.id, port.id) in incoming,
            )
            for port in definition.inputs
        )
        connectable = tuple(
            PortViewModel(
                node.id,
                parameter.id,
                parameter.label,
                parameter.connected_port_type.value
                if parameter.connected_port_type is not None
                else parameter.value_type.value,
                False,
                is_parameter=True,
                connected=(node.id, parameter.id) in incoming,
            )
            for parameter in definition.parameters
            if parameter.connectable
        )
        outputs = tuple(
            PortViewModel(
                node.id,
                port.id,
                port.label,
                _type_name(definition, port.id, True, parameters),
                True,
            )
            for port in definition.outputs
        )
        for port in (*inputs, *connectable, *outputs):
            type_names[(node.id, port.port_id, port.is_output)] = port.type_name
        parameter_rows = tuple(
            ParameterViewModel(
                parameter,
                node.parameters.get(parameter.id, parameter.default),
                (node.id, parameter.id) in incoming,
            )
            for parameter in definition.parameters
        )
        nodes.append(
            NodeViewModel(
                node.id,
                node.type_id,
                node.user_label or definition.display_name,
                definition.category,
                definition.description,
                node.position,
                (*inputs, *connectable),
                outputs,
                parameter_rows,
                tuple(node_issues.get(node.id, ())),
                node.collapsed,
            )
        )

    connections = tuple(
        ConnectionViewModel(
            connection.id,
            connection.source_node_id,
            connection.source_port_id,
            connection.destination_node_id,
            connection.destination_port_id,
            type_names.get((connection.source_node_id, connection.source_port_id, True), "GENERIC"),
            tuple(connection_issues.get(connection.id, ())),
        )
        for connection in snapshot.connections
    )
    return GraphViewModel(tuple(nodes), connections)


def _type_name(
    definition: NodeDefinition,
    port_id: str,
    is_output: bool,
    parameters: Mapping[str, ParameterValue],
) -> str:
    expression = definition.port_type(port_id, is_output=is_output, parameters=parameters)
    if isinstance(expression, PortType):
        return expression.value
    return expression.name
