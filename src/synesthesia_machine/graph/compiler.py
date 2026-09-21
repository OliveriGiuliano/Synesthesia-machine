"""Deterministic validation and compilation of immutable graph snapshots."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from synesthesia_machine.contracts import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from synesthesia_machine.contracts.runtime_values import ParameterValue, PortType
from synesthesia_machine.graph.model import ConnectionModel, GraphSnapshot, NodeModel
from synesthesia_machine.nodes import (
    ArrayTypeVariable,
    CachePolicy,
    ExecutionKind,
    NodeDefinition,
    NodeRegistry,
    TypeVariable,
)
from synesthesia_machine.runtime.execution_plan import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    ScalarConversion,
)


@dataclass(frozen=True, slots=True)
class CompilationResult:
    report: ValidationReport
    plan: ExecutionPlan | None
    # Concrete type settled by inference for every port of every known node.
    # Populated even when the graph is invalid (plan is None), so dependents
    # (e.g. the view-model projection) keep type-variable ports' resolved
    # identity while an unrelated authoring error prevents a full plan. A
    # ``None`` value marks a port whose type variable could not be settled;
    # every other port of a known node has an entry, so querying a wrong
    # port key through :meth:`resolved_type` raises instead of degrading
    # silently.
    resolved_types: Mapping[tuple[UUID, str, bool], PortType | None]

    def resolved_type(self, node_id: UUID, port_id: str, *, is_output: bool) -> PortType | None:
        """Named lookup for a port's settled type.

        Raises ``KeyError`` for a port that was never recorded (unknown node
        or port, or a direction that side does not have) so a wrong key is a
        loud error; returns ``None`` only for a known port whose type
        variable stayed unsettled.
        """

        key = (node_id, port_id, is_output)
        try:
            return self.resolved_types[key]
        except KeyError:
            side = "output" if is_output else "input"
            message = f"no {side}-side type recorded for port {port_id!r} of node {node_id}"
            raise KeyError(message) from None


@dataclass(frozen=True, slots=True)
class ConnectionCompatibility:
    """Result of validating one prospective authoring connection."""

    accepted: bool
    issues: tuple[ValidationIssue, ...] = ()


def _issue_identity(issue: ValidationIssue) -> tuple[object, ...]:
    """Stable identity of a diagnostic, excluding its presentation message.

    Diagnostic text embeds variable details: a variadic family reports how
    many sockets are connected, so the same pre-existing issue reworded by a
    count change would compare unequal on its message and make a prospective
    edge that merely improves an undersized family look incompatible.
    Location-based identity keeps "new errors caused by this edge" semantics
    while tolerating that text drift.
    """
    return (issue.severity, issue.code, issue.node_id, issue.port_id, issue.connection_id)


class _TypeGroups:
    def __init__(self) -> None:
        self._parent: dict[tuple[UUID, str], tuple[UUID, str]] = {}
        self._candidates: dict[tuple[UUID, str], set[PortType]] = defaultdict(set)
        self._allowed: dict[tuple[UUID, str], set[PortType]] = {}

    def add(self, key: tuple[UUID, str], allowed_types: frozenset[PortType]) -> None:
        self._parent.setdefault(key, key)
        if allowed_types:
            current = self._allowed.get(key)
            allowed = set(allowed_types)
            self._allowed[key] = allowed if current is None else current & allowed

    def find(self, key: tuple[UUID, str]) -> tuple[UUID, str]:
        parent = self._parent[key]
        if parent != key:
            parent = self.find(parent)
            self._parent[key] = parent
        return parent

    def union(self, left: tuple[UUID, str], right: tuple[UUID, str]) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        root, child = sorted((left_root, right_root), key=lambda item: (str(item[0]), item[1]))
        self._parent[child] = root
        self._candidates[root].update(self._candidates.pop(child, set()))
        left_allowed = self._allowed.pop(root, None)
        right_allowed = self._allowed.pop(child, None)
        if left_allowed is None and right_allowed is not None:
            self._allowed[root] = right_allowed
        elif left_allowed is not None and right_allowed is None:
            self._allowed[root] = left_allowed
        elif left_allowed is not None and right_allowed is not None:
            self._allowed[root] = left_allowed & right_allowed

    def constrain(self, key: tuple[UUID, str], value_type: PortType) -> None:
        self._candidates[self.find(key)].add(value_type)

    def resolve(self, key: tuple[UUID, str]) -> PortType | None:
        candidates = self._candidates.get(self.find(key), set())
        allowed = self._allowed.get(self.find(key))
        if allowed is not None and not candidates <= allowed:
            return None
        if not candidates:
            return None
        if len(candidates) == 1:
            return next(iter(candidates))
        if candidates <= {PortType.INT, PortType.FLOAT}:
            return PortType.FLOAT
        return None

    def incompatible_candidates(self, key: tuple[UUID, str]) -> frozenset[PortType]:
        candidates = self._candidates.get(self.find(key), set())
        allowed = self._allowed.get(self.find(key))
        if allowed is not None and not candidates <= allowed:
            return frozenset(candidates - allowed or candidates)
        if len(candidates) <= 1 or candidates <= {PortType.INT, PortType.FLOAT}:
            return frozenset()
        return frozenset(candidates)


def _settled_type(
    resolved_types: Mapping[tuple[UUID, str, bool], PortType | None],
    key: tuple[UUID, str, bool],
) -> PortType:
    """Plan-construction view of the resolved-type table.

    A plan is only built for valid reports, where every referenced port has a
    settled type; a ``None`` entry here means the invariant was violated.
    """

    value = resolved_types[key]
    if value is None:
        msg = f"plan construction hit unsettled port {key[1]} of node {key[0]}"
        raise RuntimeError(msg)
    return value


@dataclass(frozen=True, slots=True)
class _Analysis:
    """Intermediate values of the deterministic validation pipeline.

    Shared by :meth:`GraphCompiler.compile` (which adds plan construction)
    and :meth:`GraphCompiler.validate` (which stops at the report), so the
    two entry points cannot drift apart.
    """

    report: ValidationReport
    nodes: Mapping[UUID, NodeModel]
    definitions: Mapping[UUID, NodeDefinition]
    parameters: Mapping[UUID, Mapping[str, ParameterValue]]
    connections: tuple[ConnectionModel, ...]
    resolved_types: Mapping[tuple[UUID, str, bool], PortType | None]
    order: tuple[UUID, ...]
    clocks: Mapping[UUID, UUID | None]
    roots: frozenset[UUID]
    demanded: frozenset[UUID]


class GraphCompiler:
    def __init__(self, registry: NodeRegistry) -> None:
        self._registry = registry

    def compile(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
    ) -> CompilationResult:
        """Validate the snapshot and compile a plan for its valid parts.

        A fully valid snapshot compiles exactly as before. When the report
        contains errors, the invalid parts are isolated — excluded nodes and
        connections, re-validated to a fixed point — and the plan is built
        for the maximal valid remainder, so the engine can keep running the
        parts that are still sound (ADR-0029). The returned report is always
        the one for the whole document: every issue stays visible, including
        the ones in parts the plan does not use. ``plan`` is ``None`` only
        when no valid remainder exists at all.
        """
        requested = None if demand_roots is None else tuple(demand_roots)
        full = self._analyze(snapshot, requested)
        if full.report.is_valid:
            return CompilationResult(
                report=full.report,
                plan=self._build_plan(snapshot, full),
                resolved_types=full.resolved_types,
            )

        current = snapshot
        analysis = full
        while True:
            reduced = self._reduced_snapshot(current, analysis)
            if reduced is current:
                # Nothing more can be excluded. The remaining errors may all
                # be demand roots the document cannot supply: re-check with
                # the roots filtered to the surviving nodes before giving up.
                surviving = self._surviving_roots(requested, current)
                if surviving != requested:
                    final = self._analyze(current, surviving)
                    if final.report.is_valid:
                        return CompilationResult(
                            report=full.report,
                            plan=self._build_plan(snapshot, final),
                            resolved_types=full.resolved_types,
                        )
                return CompilationResult(
                    report=full.report, plan=None, resolved_types=full.resolved_types
                )
            current = reduced
            analysis = self._analyze(current, self._surviving_roots(requested, current))
            if analysis.report.is_valid:
                return CompilationResult(
                    report=full.report,
                    plan=self._build_plan(snapshot, analysis),
                    resolved_types=full.resolved_types,
                )

    def _build_plan(self, snapshot: GraphSnapshot, analysis: _Analysis) -> ExecutionPlan:
        """Build the execution plan for ``analysis`` under the snapshot's identity.

        ``analysis`` may describe a reduced subgraph (partial compilation,
        ADR-0029): the plan then keeps the original document id and revision
        so revisions and acknowledgements track the user's document, not the
        compiler's intermediate snapshots.
        """
        compiled_nodes = tuple(
            self._compile_node(
                analysis.nodes[node_id],
                analysis.definitions[node_id],
                analysis.parameters[node_id],
                analysis.connections,
                analysis.resolved_types,
                analysis.clocks[node_id],
                node_id in analysis.demanded,
            )
            for node_id in analysis.order
        )
        return ExecutionPlan(
            document_id=snapshot.document_id,
            graph_revision=snapshot.revision,
            nodes=compiled_nodes,
            demand_roots=analysis.roots,
        )

    @staticmethod
    def _reduced_snapshot(snapshot: GraphSnapshot, analysis: _Analysis) -> GraphSnapshot:
        """Exclude the invalid parts identified by ``analysis``.

        Nodes are dropped when an error attaches to them or any of their
        ports stayed unsettled by type resolution; connections are dropped
        when an error attaches to them or an endpoint is dropped. The input
        is returned unchanged when nothing can be removed, which marks the
        end of the reduction loop.
        """
        node_ids = {node.id for node in snapshot.nodes}
        dropped_nodes: set[UUID] = set()
        dropped_connections: set[UUID] = set()
        for issue in analysis.report.issues:
            if issue.severity is not ValidationSeverity.ERROR:
                continue
            if issue.node_id is not None:
                dropped_nodes.add(issue.node_id)
            if issue.connection_id is not None:
                dropped_connections.add(issue.connection_id)
        for (node_id, _port_id, _is_output), value in analysis.resolved_types.items():
            if value is None:
                dropped_nodes.add(node_id)
        dropped_nodes &= node_ids
        kept_nodes = tuple(node for node in snapshot.nodes if node.id not in dropped_nodes)
        kept_connections = tuple(
            connection
            for connection in snapshot.connections
            if connection.id not in dropped_connections
            and connection.source_node_id not in dropped_nodes
            and connection.destination_node_id not in dropped_nodes
        )
        if not dropped_nodes and len(kept_connections) == len(snapshot.connections):
            return snapshot
        return replace(snapshot, nodes=kept_nodes, connections=kept_connections)

    @staticmethod
    def _surviving_roots(
        requested: tuple[UUID, ...] | None, snapshot: GraphSnapshot
    ) -> tuple[UUID, ...] | None:
        """Explicitly requested demand roots that still exist in ``snapshot``.

        A reduction pass can remove a node the caller explicitly demanded
        (for example a pinned preview on a broken branch); demanding the
        ghost id would only raise ``unknown_demand_root`` and pin the
        reduction loop without excluding anything.
        """
        if requested is None:
            return None
        present = {node.id for node in snapshot.nodes}
        return tuple(root for root in requested if root in present)

    def validate(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
    ) -> ValidationReport:
        """Run the full deterministic validation pipeline without plan construction.

        The report is byte-for-byte the one :meth:`compile` would produce; callers that
        only ask "is this graph valid?" (precondition checks, prospective-connection
        queries, randomization acceptance loops) avoid building an ``ExecutionPlan``.
        """

        return self._analyze(snapshot, demand_roots).report

    def _analyze(
        self,
        snapshot: GraphSnapshot,
        demand_roots: Iterable[UUID] | None,
    ) -> _Analysis:
        issues: list[ValidationIssue] = []
        nodes = self._unique_nodes(snapshot, issues)
        definitions, parameters = self._resolve_definitions(nodes, issues)
        connections = self._validate_connections(snapshot, nodes, definitions, issues)
        self._validate_cardinality(connections, issues)
        resolved_types = self._resolve_types(connections, definitions, parameters, issues)
        order = self._topological_order(nodes, connections, issues)
        self._validate_required_inputs(connections, definitions, parameters, issues)
        clocks = self._analyze_clocks(order, definitions, connections, issues)
        roots = self._resolve_demand_roots(nodes, definitions, connections, demand_roots, issues)
        demanded = self._upstream_reachable(roots, connections)

        return _Analysis(
            report=ValidationReport(tuple(issues)),
            nodes=nodes,
            definitions=definitions,
            parameters=parameters,
            connections=connections,
            resolved_types=resolved_types,
            order=order,
            clocks=clocks,
            roots=frozenset(roots),
            demanded=demanded,
        )

    def connection_compatibility(
        self,
        snapshot: GraphSnapshot,
        source_node_id: UUID,
        source_port_id: str,
        destination_node_id: UUID,
        destination_port_id: str,
    ) -> ConnectionCompatibility:
        """Validate a prospective edge while tolerating unrelated authoring errors.

        Occupancy replacement is modeled before comparison. New compiler errors caused by
        the candidate are returned; pre-existing incomplete-graph diagnostics do not make an
        otherwise compatible authoring connection impossible.
        """

        endpoint = (
            source_node_id,
            source_port_id,
            destination_node_id,
            destination_port_id,
        )
        return self.connection_compatibilities(snapshot, (endpoint,))[endpoint]

    def connection_compatibilities(
        self,
        snapshot: GraphSnapshot,
        candidates: Iterable[tuple[UUID, str, UUID, str]],
    ) -> dict[tuple[UUID, str, UUID, str], ConnectionCompatibility]:
        """Validate several prospective edges while sharing identical baselines.

        A prospective edge is rejected only for diagnostics absent from the
        baseline. Diagnostics are compared by location, not message, because
        message text can reword the same pre-existing issue (a variadic
        family below its minimum reports the connected socket count), and an
        edge that merely improves an undersized family must stay offerable.
        """

        results: dict[tuple[UUID, str, UUID, str], ConnectionCompatibility] = {}
        baseline_cache: dict[tuple[ConnectionModel, ...], set[tuple[object, ...]]] = {}
        for endpoint in candidates:
            source_node_id, source_port_id, destination_node_id, destination_port_id = endpoint
            retained = tuple(
                connection
                for connection in snapshot.connections
                if not (
                    connection.destination_node_id == destination_node_id
                    and connection.destination_port_id == destination_port_id
                )
            )
            baseline_identities = baseline_cache.get(retained)
            if baseline_identities is None:
                base = replace(snapshot, connections=retained)
                baseline_identities = {
                    _issue_identity(issue) for issue in self.validate(base).issues
                }
                baseline_cache[retained] = baseline_identities
            candidate = ConnectionModel(
                id=uuid5(
                    snapshot.document_id,
                    f"connection-query:{source_node_id}:{source_port_id}:"
                    f"{destination_node_id}:{destination_port_id}",
                ),
                source_node_id=source_node_id,
                source_port_id=source_port_id,
                destination_node_id=destination_node_id,
                destination_port_id=destination_port_id,
            )
            prospective = replace(snapshot, connections=(*retained, candidate))
            new_errors = tuple(
                issue
                for issue in self.validate(prospective).errors
                if _issue_identity(issue) not in baseline_identities
            )
            results[endpoint] = ConnectionCompatibility(
                accepted=not new_errors,
                issues=new_errors,
            )
        return results

    @staticmethod
    def _unique_nodes(
        snapshot: GraphSnapshot, issues: list[ValidationIssue]
    ) -> dict[UUID, NodeModel]:
        result: dict[UUID, NodeModel] = {}
        for node in snapshot.nodes:
            if node.id in result:
                issues.append(_error("duplicate_node_id", f"Duplicate node ID {node.id}", node.id))
            else:
                result[node.id] = node
        seen_connections: set[UUID] = set()
        for connection in snapshot.connections:
            if connection.id in seen_connections:
                issues.append(
                    _error(
                        "duplicate_connection_id",
                        f"Duplicate connection ID {connection.id}",
                        connection_id=connection.id,
                    )
                )
            seen_connections.add(connection.id)
        return result

    def _resolve_definitions(
        self,
        nodes: Mapping[UUID, NodeModel],
        issues: list[ValidationIssue],
    ) -> tuple[dict[UUID, NodeDefinition], dict[UUID, dict[str, ParameterValue]]]:
        definitions: dict[UUID, NodeDefinition] = {}
        parameter_values: dict[UUID, dict[str, ParameterValue]] = {}
        for node_id in sorted(nodes, key=str):
            node = nodes[node_id]
            definition = self._registry.get(node.type_id)
            if definition is None:
                issues.append(
                    _error("unknown_node_type", f"Unknown node type {node.type_id!r}", node_id)
                )
                continue
            definitions[node_id] = definition
            if node.implementation_version != definition.execution.implementation_version:
                issues.append(
                    _error(
                        "node_version_mismatch",
                        f"Node {node.type_id!r} requires implementation version "
                        f"{definition.execution.implementation_version}, "
                        f"got {node.implementation_version}",
                        node_id,
                    )
                )
            values, errors = definition.execution.parameter_values(node.parameters)
            parameter_values[node_id] = values
            for message in errors:
                issues.append(_error("invalid_parameter", message, node_id))
        return definitions, parameter_values

    @staticmethod
    def _validate_connections(
        snapshot: GraphSnapshot,
        nodes: Mapping[UUID, NodeModel],
        definitions: Mapping[UUID, NodeDefinition],
        issues: list[ValidationIssue],
    ) -> tuple[ConnectionModel, ...]:
        valid: list[ConnectionModel] = []
        seen_ids: set[UUID] = set()
        for connection in sorted(snapshot.connections, key=lambda item: str(item.id)):
            if connection.id in seen_ids:
                continue
            seen_ids.add(connection.id)
            if connection.source_node_id not in nodes:
                issues.append(
                    _error(
                        "missing_source_node",
                        f"Connection source node {connection.source_node_id} does not exist",
                        connection_id=connection.id,
                    )
                )
                continue
            if connection.destination_node_id not in nodes:
                issues.append(
                    _error(
                        "missing_destination_node",
                        "Connection destination node "
                        f"{connection.destination_node_id} does not exist",
                        connection_id=connection.id,
                    )
                )
                continue
            source_definition = definitions.get(connection.source_node_id)
            destination_definition = definitions.get(connection.destination_node_id)
            if source_definition is None or destination_definition is None:
                continue
            if source_definition.execution.output(connection.source_port_id) is None:
                issues.append(
                    _error(
                        "unknown_output_port",
                        f"Unknown output port {connection.source_port_id!r}",
                        connection.source_node_id,
                        connection.id,
                        connection.source_port_id,
                    )
                )
                continue
            destination_parameter = destination_definition.execution.parameter(
                connection.destination_port_id
            )
            if destination_definition.execution.input(
                connection.destination_port_id
            ) is None and not (
                destination_parameter is not None and destination_parameter.connectable
            ):
                issues.append(
                    _error(
                        "unknown_input_port",
                        f"Unknown input port {connection.destination_port_id!r}",
                        connection.destination_node_id,
                        connection.id,
                        connection.destination_port_id,
                    )
                )
                continue
            valid.append(connection)
        return tuple(valid)

    @staticmethod
    def _validate_cardinality(
        connections: tuple[ConnectionModel, ...], issues: list[ValidationIssue]
    ) -> None:
        by_input: dict[tuple[UUID, str], list[ConnectionModel]] = defaultdict(list)
        for connection in connections:
            by_input[(connection.destination_node_id, connection.destination_port_id)].append(
                connection
            )
        for (node_id, port_id), incoming in by_input.items():
            if len(incoming) > 1:
                issues.append(
                    _error(
                        "input_cardinality",
                        f"Input {port_id} accepts one connection, got {len(incoming)}",
                        node_id,
                        port_id=port_id,
                    )
                )

    @staticmethod
    def _resolve_types(
        connections: tuple[ConnectionModel, ...],
        definitions: Mapping[UUID, NodeDefinition],
        parameters: Mapping[UUID, Mapping[str, ParameterValue]],
        issues: list[ValidationIssue],
    ) -> dict[tuple[UUID, str, bool], PortType | None]:
        groups = _TypeGroups()
        expressions: dict[tuple[UUID, str, bool], PortType | TypeVariable | ArrayTypeVariable] = {}
        next_only: set[tuple[UUID, str, bool]] = set()
        connected_inputs: dict[UUID, set[str]] = defaultdict(set)
        for connection in connections:
            connected_inputs[connection.destination_node_id].add(connection.destination_port_id)
        for node_id, definition in definitions.items():
            node_parameters = parameters.get(node_id, {})
            base_ids = {
                port.id for port in definition.execution.input_ports(connected_inputs[node_id])
            }
            for port in definition.execution.input_ports(
                connected_inputs[node_id], include_next_variadic=True
            ):
                expression = definition.execution.port_type(
                    port.id, is_output=False, parameters=node_parameters
                )
                expressions[(node_id, port.id, False)] = expression
                if port.id not in base_ids:
                    next_only.add((node_id, port.id, False))
                if isinstance(expression, (TypeVariable, ArrayTypeVariable)):
                    groups.add((node_id, expression.name), expression.allowed_types)
            for parameter in definition.execution.parameters:
                if not parameter.connectable or parameter.connected_port_type is None:
                    continue
                expressions[(node_id, parameter.id, False)] = parameter.connected_port_type
            for port in definition.execution.outputs:
                expression = definition.execution.port_type(
                    port.id, is_output=True, parameters=node_parameters
                )
                expressions[(node_id, port.id, True)] = expression
                if isinstance(expression, (TypeVariable, ArrayTypeVariable)):
                    groups.add((node_id, expression.name), expression.allowed_types)

        for connection in connections:
            source = expressions[(connection.source_node_id, connection.source_port_id, True)]
            destination = expressions[
                (connection.destination_node_id, connection.destination_port_id, False)
            ]
            source_key = _variable_key(connection.source_node_id, source)
            destination_key = _variable_key(connection.destination_node_id, destination)
            if source_key is not None and destination_key is not None:
                groups.union(source_key, destination_key)
            elif source_key is not None and isinstance(destination, PortType):
                assert isinstance(source, (TypeVariable, ArrayTypeVariable))
                groups.constrain(source_key, _base_type(destination, source))
            elif destination_key is not None and isinstance(source, PortType):
                assert isinstance(destination, (TypeVariable, ArrayTypeVariable))
                groups.constrain(destination_key, _base_type(source, destination))

        resolved: dict[tuple[UUID, str, bool], PortType | None] = {}
        for port_key, expression in expressions.items():
            if isinstance(expression, PortType):
                resolved[port_key] = expression
                continue
            variable_key = (port_key[0], expression.name)
            incompatible = groups.incompatible_candidates(variable_key)
            value_type = groups.resolve(variable_key)
            if incompatible:
                names = ", ".join(sorted(item.value for item in incompatible))
                if port_key not in next_only:
                    issues.append(
                        _error(
                            "generic_type_conflict",
                            f"Incompatible constraints on type variable {expression.name}: {names}",
                            port_key[0],
                            port_id=port_key[1],
                        )
                    )
                resolved[port_key] = None
            elif value_type is None:
                if port_key not in next_only:
                    issues.append(
                        _error(
                            "unresolved_generic_type",
                            f"Type variable {expression.name} could not be resolved",
                            port_key[0],
                            port_id=port_key[1],
                        )
                    )
                resolved[port_key] = None
            else:
                resolved[port_key] = (
                    _array_type(value_type)
                    if isinstance(expression, ArrayTypeVariable)
                    else value_type
                )

        for connection in connections:
            source_type = resolved.get((connection.source_node_id, connection.source_port_id, True))
            destination_type = resolved.get(
                (connection.destination_node_id, connection.destination_port_id, False)
            )
            if source_type is None or destination_type is None:
                continue
            if not types_compatible(source_type, destination_type):
                issues.append(
                    _error(
                        "incompatible_port_types",
                        f"Cannot connect {source_type.value} to {destination_type.value}",
                        connection.destination_node_id,
                        connection.id,
                        connection.destination_port_id,
                    )
                )
        return resolved

    @staticmethod
    def _topological_order(
        nodes: Mapping[UUID, NodeModel],
        connections: tuple[ConnectionModel, ...],
        issues: list[ValidationIssue],
    ) -> tuple[UUID, ...]:
        incoming_count = {node_id: 0 for node_id in nodes}
        outgoing: dict[UUID, set[UUID]] = defaultdict(set)
        for connection in connections:
            if connection.destination_node_id not in outgoing[connection.source_node_id]:
                outgoing[connection.source_node_id].add(connection.destination_node_id)
                incoming_count[connection.destination_node_id] += 1
        ready = sorted(
            (node_id for node_id, count in incoming_count.items() if count == 0), key=str
        )
        order: list[UUID] = []
        while ready:
            node_id = ready.pop(0)
            order.append(node_id)
            for destination in sorted(outgoing[node_id], key=str):
                incoming_count[destination] -= 1
                if incoming_count[destination] == 0:
                    ready.append(destination)
                    ready.sort(key=str)
        if len(order) != len(nodes):
            cyclic = sorted(
                (node_id for node_id, count in incoming_count.items() if count > 0), key=str
            )
            for node_id in cyclic:
                issues.append(_error("graph_cycle", "Node participates in a graph cycle", node_id))
        return tuple(order)

    @staticmethod
    def _validate_required_inputs(
        connections: tuple[ConnectionModel, ...],
        definitions: Mapping[UUID, NodeDefinition],
        parameters: Mapping[UUID, Mapping[str, ParameterValue]],
        issues: list[ValidationIssue],
    ) -> None:
        connected: dict[UUID, set[str]] = defaultdict(set)
        for connection in connections:
            connected[connection.destination_node_id].add(connection.destination_port_id)
        for node_id, definition in definitions.items():
            node_ports = connected.get(node_id, set())
            for port_id in sorted(
                definition.execution.required_inputs(parameters.get(node_id, {}))
            ):
                family = definition.execution.variadic_input
                if family is not None and port_id == family.id_prefix:
                    # Family-level requirement: any minimum_count of the
                    # family's sockets satisfy it, so a freed lower-index
                    # socket does not invalidate the node while the family
                    # minimum is still met by higher-index sockets.
                    count = sum(1 for pid in node_ports if family.index(pid) is not None)
                    if count < family.minimum_count:
                        issues.append(
                            _error(
                                "required_input_missing",
                                f"Input family {family.id_prefix!r} has {count} connected "
                                f"socket(s) but needs at least {family.minimum_count}",
                                node_id,
                                port_id=family.id_prefix,
                            )
                        )
                    continue
                if port_id not in node_ports:
                    issues.append(
                        _error(
                            "required_input_missing",
                            f"Required input {port_id!r} is not connected",
                            node_id,
                            port_id=port_id,
                        )
                    )

    @staticmethod
    def _analyze_clocks(
        order: tuple[UUID, ...],
        definitions: Mapping[UUID, NodeDefinition],
        connections: tuple[ConnectionModel, ...],
        issues: list[ValidationIssue],
    ) -> dict[UUID, UUID | None]:
        predecessors: dict[UUID, set[UUID]] = defaultdict(set)
        for connection in connections:
            predecessors[connection.destination_node_id].add(connection.source_node_id)
        clocks: dict[UUID, UUID | None] = {}
        for node_id in order:
            definition = definitions.get(node_id)
            if definition is None:
                continue
            if definition.execution.execution_kind is ExecutionKind.SOURCE:
                clocks[node_id] = node_id
                continue
            incoming_clocks = {
                clocks.get(predecessor)
                for predecessor in predecessors[node_id]
                if clocks.get(predecessor) is not None
            }
            if len(incoming_clocks) > 1:
                issues.append(
                    _error(
                        "clock_mismatch",
                        "Dynamic inputs originate from different source clocks",
                        node_id,
                    )
                )
                clocks[node_id] = None
            else:
                clocks[node_id] = next(iter(incoming_clocks), None)
        return clocks

    @staticmethod
    def _resolve_demand_roots(
        nodes: Mapping[UUID, NodeModel],
        definitions: Mapping[UUID, NodeDefinition],
        connections: tuple[ConnectionModel, ...],
        requested: Iterable[UUID] | None,
        issues: list[ValidationIssue],
    ) -> frozenset[UUID]:
        if requested is not None:
            roots = frozenset(requested)
            for node_id in sorted(roots - nodes.keys(), key=str):
                issues.append(
                    _error("unknown_demand_root", f"Demand root {node_id} does not exist", node_id)
                )
            return frozenset(node_id for node_id in roots if node_id in nodes)
        explicit = {
            node_id
            for node_id, definition in definitions.items()
            if definition.execution.execution_kind in {ExecutionKind.SINK, ExecutionKind.VISUALIZER}
        }
        if explicit:
            return frozenset(explicit)
        has_outgoing = {connection.source_node_id for connection in connections}
        return frozenset(node_id for node_id in nodes if node_id not in has_outgoing)

    @staticmethod
    def _upstream_reachable(
        roots: frozenset[UUID], connections: tuple[ConnectionModel, ...]
    ) -> frozenset[UUID]:
        predecessors: dict[UUID, set[UUID]] = defaultdict(set)
        for connection in connections:
            predecessors[connection.destination_node_id].add(connection.source_node_id)
        reachable = set(roots)
        pending = list(roots)
        while pending:
            node_id = pending.pop()
            for predecessor in predecessors[node_id]:
                if predecessor not in reachable:
                    reachable.add(predecessor)
                    pending.append(predecessor)
        return frozenset(reachable)

    @staticmethod
    def _compile_node(
        node: NodeModel,
        definition: NodeDefinition,
        parameters: Mapping[str, ParameterValue],
        connections: tuple[ConnectionModel, ...],
        resolved_types: Mapping[tuple[UUID, str, bool], PortType | None],
        clock_id: UUID | None,
        is_demanded: bool,
    ) -> CompiledNode:
        incoming = {
            connection.destination_port_id: connection
            for connection in connections
            if connection.destination_node_id == node.id
        }
        bindings: dict[str, InputBinding] = {}
        for port_id in _input_socket_ids(definition, incoming):
            connection = incoming.get(port_id)
            if connection is None:
                continue
            source_type = _settled_type(
                resolved_types, (connection.source_node_id, connection.source_port_id, True)
            )
            destination_type = _settled_type(resolved_types, (node.id, port_id, False))
            conversion = (
                ScalarConversion.INT_TO_FLOAT
                if source_type is PortType.INT and destination_type is PortType.FLOAT
                else None
            )
            bindings[port_id] = InputBinding(
                source=PortKey(connection.source_node_id, connection.source_port_id),
                conversion=conversion,
            )
        input_types = {
            port_id: _settled_type(resolved_types, (node.id, port_id, False))
            for port_id in _input_socket_ids(definition, incoming)
        }
        output_types = {
            port.id: _settled_type(resolved_types, (node.id, port.id, True))
            for port in definition.execution.outputs
        }
        is_static = clock_id is None and definition.execution.cache_policy is not CachePolicy.NEVER
        return CompiledNode(
            node_id=node.id,
            definition=definition.execution,
            parameters=parameters,
            input_bindings=bindings,
            input_types=input_types,
            output_types=output_types,
            clock_id=clock_id,
            is_static=is_static,
            is_demanded=is_demanded,
        )


def types_compatible(source: PortType, destination: PortType) -> bool:
    if source is destination or (source is PortType.INT and destination is PortType.FLOAT):
        return True
    # A single element may feed an array input socket: the runtime treats the
    # lone value as a one-item batch (e.g. one image into a Statistics socket
    # that otherwise carries a Buffer's ValueArray).
    if destination is PortType.SCALAR_ARRAY:
        return source in {PortType.FLOAT, PortType.INT}
    if destination is PortType.IMAGE_ARRAY:
        return source is PortType.IMAGE
    if destination is PortType.CHANNEL_ARRAY:
        return source is PortType.CHANNEL
    return False


def _input_socket_ids(
    definition: NodeDefinition, connected_port_ids: Iterable[str] = ()
) -> tuple[str, ...]:
    return (
        *(port.id for port in definition.execution.input_ports(connected_port_ids)),
        *(parameter.id for parameter in definition.execution.parameters if parameter.connectable),
    )


def _variable_key(
    node_id: UUID, expression: PortType | TypeVariable | ArrayTypeVariable
) -> tuple[UUID, str] | None:
    if isinstance(expression, (TypeVariable, ArrayTypeVariable)):
        return node_id, expression.name
    return None


def _array_type(value_type: PortType) -> PortType:
    if value_type in {PortType.FLOAT, PortType.INT}:
        return PortType.SCALAR_ARRAY
    if value_type is PortType.IMAGE:
        return PortType.IMAGE_ARRAY
    if value_type is PortType.CHANNEL:
        return PortType.CHANNEL_ARRAY
    raise ValueError(f"No array port type for {value_type.value}")


def _base_type(value_type: PortType, expression: TypeVariable | ArrayTypeVariable) -> PortType:
    if not isinstance(expression, ArrayTypeVariable):
        return value_type
    if value_type is PortType.SCALAR_ARRAY:
        return PortType.FLOAT
    if value_type is PortType.IMAGE_ARRAY:
        return PortType.IMAGE
    if value_type is PortType.CHANNEL_ARRAY:
        return PortType.CHANNEL
    return value_type


def _error(
    code: str,
    message: str,
    node_id: UUID | None = None,
    connection_id: UUID | None = None,
    port_id: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity=ValidationSeverity.ERROR,
        code=code,
        message=message,
        node_id=node_id,
        connection_id=connection_id,
        port_id=port_id,
    )
