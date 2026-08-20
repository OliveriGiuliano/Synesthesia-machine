"""Graph authoring, type-system, validation, and compiler tests."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from synesthesia_machine.contracts import PortType
from synesthesia_machine.graph import (
    CompilationResult,
    ConnectionModel,
    GraphCompiler,
    GraphDocument,
    GraphSnapshot,
    NodeModel,
    types_compatible,
)
from synesthesia_machine.nodes import ExecutionKind, InputPortSpec, NodeRegistry
from synesthesia_machine.nodes.utility import create_utility_registry
from tests.phase1.helpers import make_definition, make_tv_image_producer

NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")
NODE_C = UUID("00000000-0000-0000-0000-00000000000c")
CONNECTION_A = UUID("00000000-0000-0000-0000-00000000001a")
CONNECTION_B = UUID("00000000-0000-0000-0000-00000000001b")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000100")


@pytest.mark.parametrize("source", list(PortType))
@pytest.mark.parametrize("destination", list(PortType))
def test_all_type_compatibility_combinations(source: PortType, destination: PortType) -> None:
    expected = source is destination or (source is PortType.INT and destination is PortType.FLOAT)
    assert types_compatible(source, destination) is expected


def test_graph_document_replaces_ordinary_input_connection() -> None:
    document = GraphDocument(document_id=DOCUMENT_ID)
    first = document.add_node("test.float_source", node_id=NODE_A)
    second = document.add_node("test.float_source", node_id=NODE_B)
    target = document.add_node("test.float_sink", node_id=NODE_C)
    document.add_connection(first, "value", target, "value", connection_id=CONNECTION_A)
    document.add_connection(second, "value", target, "value", connection_id=CONNECTION_B)

    assert document.connections == (
        ConnectionModel(CONNECTION_B, NODE_B, "value", NODE_C, "value"),
    )


def test_compiler_resolves_generic_and_inserts_int_to_float_conversion() -> None:
    document = GraphDocument(document_id=DOCUMENT_ID)
    source = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "INT", "int_value": 4},
    )
    passthrough = document.add_node("synmachine.utility.pass_through", node_id=NODE_B)
    math_node = document.add_node(
        "synmachine.utility.math", node_id=NODE_C, parameters={"operation": "ABS"}
    )
    document.add_connection(source, "value", passthrough, "value")
    document.add_connection(passthrough, "value", math_node, "a")

    result = GraphCompiler(create_utility_registry()).compile(document.snapshot())

    assert result.report.is_valid
    assert result.plan is not None
    generic = result.plan.node(passthrough)
    math_compiled = result.plan.node(math_node)
    assert generic is not None and generic.input_types["value"] is PortType.FLOAT
    assert generic.output_types["value"] is PortType.FLOAT
    assert generic.input_bindings["value"].conversion == "INT_TO_FLOAT"
    assert math_compiled is not None
    assert math_compiled.input_bindings["a"].conversion is None


def test_compiler_reports_unresolved_and_conflicting_generics() -> None:
    registry = create_utility_registry()
    unresolved = GraphDocument(document_id=DOCUMENT_ID)
    unresolved.add_node("synmachine.utility.pass_through", node_id=NODE_A)
    unresolved_result = GraphCompiler(registry).compile(unresolved.snapshot())
    assert "unresolved_generic_type" in _codes(unresolved_result)

    conflict = GraphDocument(document_id=DOCUMENT_ID)
    integer = conflict.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "INT", "int_value": 1},
    )
    generic = conflict.add_node("synmachine.utility.conditional", node_id=NODE_B)
    boolean = conflict.add_node("test.bool_source", node_id=NODE_C)
    float_node_id = UUID("00000000-0000-0000-0000-00000000000d")
    float_source = conflict.add_node(
        "synmachine.utility.number",
        node_id=float_node_id,
        parameters={"number_type": "FLOAT", "float_value": 1.0},
    )
    conflict.add_connection(boolean, "value", generic, "condition")
    conflict.add_connection(integer, "value", generic, "if_true")
    conflict.add_connection(float_source, "value", generic, "if_false")
    combined = NodeRegistry(
        (*registry.definitions(), make_definition("test.bool_source", output_type=PortType.BOOL))
    )
    resolved_result = GraphCompiler(combined).compile(conflict.snapshot())
    assert resolved_result.report.is_valid
    assert resolved_result.plan is not None
    compiled_generic = resolved_result.plan.node(generic)
    assert compiled_generic is not None
    assert compiled_generic.output_types["value"] is PortType.FLOAT

    string_definition = make_definition("test.string_source", output_type=PortType.STRING)
    conflict = GraphDocument(document_id=DOCUMENT_ID)
    bool_source = conflict.add_node("test.bool_source", node_id=NODE_A)
    float_source = conflict.add_node("synmachine.utility.number", node_id=NODE_B)
    string_source = conflict.add_node("test.string_source", node_id=NODE_C)
    conditional = conflict.add_node(
        "synmachine.utility.conditional",
        node_id=UUID("00000000-0000-0000-0000-00000000000d"),
    )
    conflict.add_connection(bool_source, "value", conditional, "condition")
    conflict.add_connection(float_source, "value", conditional, "if_true")
    conflict.add_connection(string_source, "value", conditional, "if_false")
    combined = NodeRegistry(
        (
            *registry.definitions(),
            make_definition("test.bool_source", output_type=PortType.BOOL),
            string_definition,
        )
    )
    assert "generic_type_conflict" in _codes(GraphCompiler(combined).compile(conflict.snapshot()))


@pytest.mark.parametrize("self_loop", [False, True])
def test_compiler_rejects_cycles(self_loop: bool) -> None:
    registry = NodeRegistry((make_definition("test.float", input_type=PortType.FLOAT),))
    document = GraphDocument(document_id=DOCUMENT_ID)
    first = document.add_node("test.float", node_id=NODE_A)
    second = first if self_loop else document.add_node("test.float", node_id=NODE_B)
    document.add_connection(first, "value", second, "value")
    if not self_loop:
        document.add_connection(second, "value", first, "value")

    assert "graph_cycle" in _codes(GraphCompiler(registry).compile(document.snapshot()))


def test_compiler_reports_cardinality_required_ports_and_unknown_references() -> None:
    registry = NodeRegistry(
        (
            make_definition("test.source"),
            make_definition("test.sink", input_type=PortType.FLOAT),
        )
    )
    nodes = (
        NodeModel(NODE_A, "test.source", 1),
        NodeModel(NODE_B, "test.source", 1),
        NodeModel(NODE_C, "test.sink", 1),
    )
    snapshot = GraphSnapshot(
        DOCUMENT_ID,
        0,
        nodes,
        (
            ConnectionModel(CONNECTION_A, NODE_A, "value", NODE_C, "value"),
            ConnectionModel(CONNECTION_B, NODE_B, "value", NODE_C, "value"),
        ),
    )
    assert "input_cardinality" in _codes(GraphCompiler(registry).compile(snapshot))

    missing = GraphSnapshot(DOCUMENT_ID, 0, nodes, ())
    assert "required_input_missing" in _codes(GraphCompiler(registry).compile(missing))

    invalid = GraphSnapshot(
        DOCUMENT_ID,
        0,
        nodes,
        (ConnectionModel(CONNECTION_A, NODE_A, "missing", NODE_C, "value"),),
    )
    assert "unknown_output_port" in _codes(GraphCompiler(registry).compile(invalid))


def test_compiler_rejects_clock_mismatch_and_orders_deterministically() -> None:
    registry = NodeRegistry(
        (
            make_definition("test.source", execution_kind=ExecutionKind.SOURCE),
            make_definition("test.merge", input_type=PortType.FLOAT),
        )
    )
    # The merge definition has one ordinary input, so use two distinct input IDs explicitly.
    merge_definition = registry.require("test.merge")
    merge_definition = replace(
        merge_definition,
        inputs=(
            merge_definition.inputs[0],
            InputPortSpec("other", "Other", PortType.FLOAT),
        ),
    )
    registry = NodeRegistry((registry.require("test.source"), merge_definition))
    document = GraphDocument(document_id=DOCUMENT_ID)
    later_source = document.add_node("test.source", node_id=NODE_B)
    earlier_source = document.add_node("test.source", node_id=NODE_A)
    merge = document.add_node("test.merge", node_id=NODE_C)
    document.add_connection(later_source, "value", merge, "value")
    document.add_connection(earlier_source, "value", merge, "other")
    result = GraphCompiler(registry).compile(document.snapshot())
    assert "clock_mismatch" in _codes(result)
    assert result.plan is None

    independent = GraphDocument(document_id=DOCUMENT_ID)
    independent.add_node("test.source", node_id=NODE_B)
    independent.add_node("test.source", node_id=NODE_A)
    ordered = GraphCompiler(registry).compile(independent.snapshot())
    assert ordered.plan is not None
    assert ordered.plan.topological_node_ids == (NODE_A, NODE_B)


def test_compiler_exposes_resolved_types_for_invalid_graphs() -> None:
    """A type-variable port keeps its resolved type in the result even when an unrelated
    floating node makes the graph invalid (plan is None), so dependents (e.g. the view-model
    projection and its link pills) do not lose the port's resolved identity."""
    registry = NodeRegistry(
        (
            make_tv_image_producer(),
            make_definition(
                "test.image_sink", input_type=PortType.IMAGE, output_type=PortType.IMAGE
            ),
            make_definition("test.floaty", input_type=PortType.IMAGE, output_type=PortType.IMAGE),
        )
    )

    valid = GraphDocument(document_id=DOCUMENT_ID)
    source = valid.add_node("test.tv_image_producer", node_id=NODE_A)
    sink = valid.add_node("test.image_sink", node_id=NODE_B)
    valid.add_connection(source, "value", sink, "value")
    valid_result = GraphCompiler(registry).compile(valid.snapshot())
    assert valid_result.report.is_valid
    assert valid_result.plan is not None
    assert valid_result.resolved_types[(NODE_A, "value", True)] is PortType.IMAGE

    invalid = GraphDocument(document_id=DOCUMENT_ID)
    source = invalid.add_node("test.tv_image_producer", node_id=NODE_A)
    sink = invalid.add_node("test.image_sink", node_id=NODE_B)
    invalid.add_connection(source, "value", sink, "value")
    invalid.add_node("test.floaty", node_id=NODE_C)
    invalid_result = GraphCompiler(registry).compile(invalid.snapshot())
    assert invalid_result.report.is_valid is False
    assert invalid_result.plan is None
    assert "required_input_missing" in _codes(invalid_result)
    # The producer's type-variable output stays resolved to IMAGE despite the invalid graph.
    assert invalid_result.resolved_types[(NODE_A, "value", True)] is PortType.IMAGE


def _codes(result: CompilationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}
