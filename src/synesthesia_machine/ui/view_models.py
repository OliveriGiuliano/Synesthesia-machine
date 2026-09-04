"""Immutable projections from graph-domain values to editor rendering data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import UUID

from synesthesia_machine.contracts import ParameterValue, PortType
from synesthesia_machine.graph import (
    CompilationResult,
    GraphCompiler,
    GraphSnapshot,
    LiteralValue,
    ValidationIssue,
    ValidationReport,
)
from synesthesia_machine.nodes import (
    NodeDefinition,
    NodeRegistry,
    ParameterGroupSpec,
    ParameterSpec,
)
from synesthesia_machine.ui.translations import tr


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
class ParameterGroupViewModel:
    spec: ParameterGroupSpec
    parameters: tuple[ParameterViewModel, ...]


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
    parameter_groups: tuple[ParameterGroupViewModel, ...]
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
    *,
    compilation: CompilationResult | None = None,
) -> GraphViewModel:
    compilation = compilation or GraphCompiler(registry).compile(snapshot)
    # The compiler settles a concrete type for every resolvable port even when the
    # graph is otherwise invalid (plan is None). Projecting those keeps type-variable
    # ports' resolved identity (e.g. an IMAGE link) stable while an unrelated
    # authoring error blocks the plan, so link pills are not dropped or re-typed.
    resolved_types: Mapping[tuple[UUID, str, bool], PortType] = compilation.resolved_types
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
        connected_port_ids = {
            connection.destination_port_id
            for connection in snapshot.connections
            if connection.destination_node_id == node.id
        }
        inputs = tuple(
            PortViewModel(
                node.id,
                port.id,
                tr(port.label),
                resolved_types[(node.id, port.id, False)].value
                if (node.id, port.id, False) in resolved_types
                else _type_name(definition, port.id, False, parameters),
                False,
                connected=(node.id, port.id) in incoming,
            )
            for port in definition.input_ports(connected_port_ids, include_next_variadic=True)
        )
        connectable = tuple(
            PortViewModel(
                node.id,
                parameter.id,
                tr(parameter.label),
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
                tr(port.label),
                resolved_types[(node.id, port.id, True)].value
                if (node.id, port.id, True) in resolved_types
                else _type_name(definition, port.id, True, parameters),
                True,
            )
            for port in definition.outputs
        )
        for port in (*inputs, *connectable, *outputs):
            type_names[(node.id, port.port_id, port.is_output)] = port.type_name
        primary_input_type = next(
            (
                resolved_types[(node.id, port.id, False)]
                for port in definition.inputs
                if (node.id, port.id, False) in resolved_types
            ),
            None,
        )
        parameter_rows = tuple(
            ParameterViewModel(
                replace(
                    parameter,
                    label=tr(parameter.label),
                    help_text=tr(parameter.help_text),
                ),
                node.parameters.get(parameter.id, parameter.default),
                (node.id, parameter.id) in incoming,
            )
            for parameter in definition.parameters
            if not parameter.applicable_input_types
            or primary_input_type is None
            or primary_input_type in parameter.applicable_input_types
        )
        parameter_by_id = {parameter.spec.id: parameter for parameter in parameter_rows}
        parameter_groups = tuple(
            ParameterGroupViewModel(
                replace(group, label=tr(group.label)),
                tuple(
                    parameter_by_id[parameter_id]
                    for parameter_id in group.parameter_ids
                    if parameter_id in parameter_by_id
                ),
            )
            for group in definition.parameter_groups
            if any(parameter_id in parameter_by_id for parameter_id in group.parameter_ids)
        )
        nodes.append(
            NodeViewModel(
                node.id,
                node.type_id,
                node.user_label or tr(definition.display_name),
                # Keep the category untranslated: it is a stable palette key
                # (node_category_color) as well as a display string; display
                # sites translate it themselves.
                definition.category,
                tr(definition.description),
                node.position,
                (*inputs, *connectable),
                outputs,
                parameter_rows,
                parameter_groups,
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
