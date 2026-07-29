"""Qt-free restoration and interactive compiler-query coverage."""

from uuid import UUID

from synesthesia_machine.graph import ConnectionModel, GraphCompiler, GraphDocument, NodeModel
from synesthesia_machine.nodes.utility import create_utility_registry

NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")
NODE_C = UUID("00000000-0000-0000-0000-00000000000c")
CONNECTION = UUID("00000000-0000-0000-0000-00000000001a")


def test_complete_model_values_can_be_restored_exactly() -> None:
    document = GraphDocument()
    node = NodeModel(
        NODE_A,
        "synmachine.utility.number",
        1,
        parameters={"number_type": "INT", "int_value": 7},
        position=(10.0, 20.0),
        size=(240.0, 120.0),
        user_label="Seven",
        collapsed=True,
        ui_state={"tab": "main"},
    )
    target = NodeModel(NODE_B, "synmachine.utility.math", 1)
    edge = ConnectionModel(CONNECTION, NODE_A, "value", NODE_B, "a")

    document.restore_node(node)
    document.restore_node(target)
    document.restore_connection(edge)

    assert document.node(NODE_A) == node
    assert document.connection(CONNECTION) == edge
    assert document.incoming_connection(NODE_B, "a") == edge
    assert document.incident_connections({NODE_A}) == (edge,)


def test_connection_query_uses_widening_generics_and_rejects_incompatibility() -> None:
    registry = create_utility_registry()
    compiler = GraphCompiler(registry)
    document = GraphDocument()
    integer = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "INT", "int_value": 1},
    )
    generic = document.add_node("synmachine.utility.pass_through", node_id=NODE_B)
    conditional = document.add_node("synmachine.utility.conditional", node_id=NODE_C)

    assert compiler.connection_compatibility(
        document.snapshot(), integer, "value", generic, "value"
    ).accepted
    document.add_connection(integer, "value", generic, "value")
    assert (
        compiler.connection_compatibility(
            document.snapshot(), generic, "value", conditional, "condition"
        ).accepted
        is False
    )
    assert compiler.connection_compatibility(
        document.snapshot(), generic, "value", conditional, "if_true"
    ).accepted


def test_connection_query_rejects_new_cycle_but_tolerates_incomplete_graph() -> None:
    compiler = GraphCompiler(create_utility_registry())
    document = GraphDocument()
    first = document.add_node("synmachine.utility.pass_through", node_id=NODE_A)
    second = document.add_node("synmachine.utility.pass_through", node_id=NODE_B)
    assert compiler.connection_compatibility(
        document.snapshot(), first, "value", second, "value"
    ).accepted
    document.add_connection(first, "value", second, "value")

    result = compiler.connection_compatibility(document.snapshot(), second, "value", first, "value")
    assert result.accepted is False
    assert "graph_cycle" in {issue.code for issue in result.issues}
