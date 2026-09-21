"""Qt-free restoration and interactive compiler-query coverage."""

from collections.abc import Iterable
from uuid import UUID

import pytest

from synesthesia_machine.graph import (
    CompilationResult,
    ConnectionModel,
    GraphCompiler,
    GraphDocument,
    GraphSnapshot,
    NodeModel,
    ValidationReport,
)
from synesthesia_machine.nodes.utility import create_utility_registry

NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")
NODE_C = UUID("00000000-0000-0000-0000-00000000000c")
CONNECTION = UUID("00000000-0000-0000-0000-00000000001a")
CONNECTION_B = UUID("00000000-0000-0000-0000-00000000001b")


def test_complete_model_values_can_be_restored_exactly() -> None:
    document = GraphDocument()
    node = NodeModel(
        NODE_A,
        "synmachine.utility.number",
        2,
        parameters={"number_type": "INT", "value": 7.0},
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


def test_connection_replacement_failure_is_atomic() -> None:
    document = GraphDocument()
    source_a = document.add_node(
        "synmachine.utility.number", implementation_version=2, node_id=NODE_A
    )
    source_b = document.add_node(
        "synmachine.utility.number", implementation_version=2, node_id=NODE_B
    )
    destination = document.add_node("synmachine.utility.math", node_id=NODE_C)
    original = document.add_connection(
        source_a, "value", destination, "a", connection_id=CONNECTION
    )
    document.add_connection(source_a, "value", destination, "b", connection_id=CONNECTION_B)
    before = document.snapshot()

    try:
        document.add_connection(
            source_b,
            "value",
            destination,
            "a",
            connection_id=CONNECTION_B,
            replace_existing=True,
        )
    except ValueError as error:
        assert "Connection already exists" in str(error)
    else:
        raise AssertionError("connection ID collision should fail")

    assert document.snapshot() == before
    assert document.incoming_connection(destination, "a").id == original  # type: ignore[union-attr]


def test_connection_query_uses_widening_generics_and_rejects_incompatibility() -> None:
    registry = create_utility_registry()
    compiler = GraphCompiler(registry)
    document = GraphDocument()
    integer = document.add_node(
        "synmachine.utility.number",
        implementation_version=2,
        node_id=NODE_A,
        parameters={"number_type": "INT", "value": 1.0},
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


def test_batch_connection_query_validates_shared_baseline_once() -> None:
    class _CountingCompiler(GraphCompiler):
        def __init__(self) -> None:
            super().__init__(create_utility_registry())
            self.compile_count = 0
            self.validate_count = 0

        def compile(
            self,
            snapshot: GraphSnapshot,
            *,
            demand_roots: Iterable[UUID] | None = None,
        ) -> CompilationResult:
            self.compile_count += 1
            return super().compile(snapshot, demand_roots=demand_roots)

        def validate(
            self,
            snapshot: GraphSnapshot,
            *,
            demand_roots: Iterable[UUID] | None = None,
        ) -> ValidationReport:
            self.validate_count += 1
            return super().validate(snapshot, demand_roots=demand_roots)

    compiler = _CountingCompiler()
    document = GraphDocument()
    source = document.add_node(
        "synmachine.utility.number",
        implementation_version=2,
        parameters={"number_type": "FLOAT", "value": 1.0},
    )
    destination = document.add_node("synmachine.utility.math")
    candidates = (
        (source, "value", destination, "a"),
        (source, "value", destination, "b"),
    )

    results = compiler.connection_compatibilities(document.snapshot(), candidates)

    assert all(results[candidate].accepted for candidate in candidates)
    # The identical baseline is validated once and shared; each candidate is
    # validated on its own, and no plan is ever constructed.
    assert compiler.validate_count == 3
    assert compiler.compile_count == 0


def test_connection_query_offers_first_edge_into_undersized_midi_family() -> None:
    compiler = GraphCompiler(create_utility_registry())
    document = GraphDocument()
    first = document.add_node("synmachine.utility.transpose", node_id=NODE_A)
    second = document.add_node("synmachine.utility.transpose", node_id=NODE_B)
    merge = document.add_node("synmachine.utility.midi_merge", node_id=NODE_C)

    # MIDI Merge needs at least two family sockets, so this first edge still
    # leaves the family undersized. That incompleteness pre-exists the edge
    # (its diagnostic only changes the reported count), so both sockets must
    # stay offerable and the node must be wireable from an empty start.
    assert compiler.connection_compatibility(
        document.snapshot(), first, "midi", merge, "midi_1"
    ).accepted
    document.add_connection(first, "midi", merge, "midi_1")
    assert compiler.connection_compatibility(
        document.snapshot(), second, "midi", merge, "midi_2"
    ).accepted
    document.add_connection(second, "midi", merge, "midi_2")
    issues = compiler.compile(document.snapshot()).report.errors
    # The merge family is now satisfied; the only remaining errors are the
    # intentionally unconnected MIDI inputs of the transpose stand-ins.
    assert {issue.code for issue in issues} == {"required_input_missing"}
    assert {issue.node_id for issue in issues} == {NODE_A, NODE_B}

    # Genuine edge incompatibility is still rejected: a MIDI state cannot feed
    # the conditional's concrete boolean input.
    boolean = document.add_node("synmachine.utility.conditional")
    rejected = compiler.connection_compatibility(
        document.snapshot(), first, "midi", boolean, "condition"
    )
    assert rejected.accepted is False
    assert "incompatible_port_types" in {issue.code for issue in rejected.issues}


def test_clear_parameter_restores_the_definition_default() -> None:
    document = GraphDocument()
    node_id = document.add_node(
        "synmachine.utility.number",
        implementation_version=2,
        node_id=NODE_A,
        parameters={"number_type": "INT", "value": 7.0},
    )
    document.set_parameter(node_id, "value", 9.0)
    assert document.node(node_id).parameters == {"number_type": "INT", "value": 9.0}  # type: ignore[union-attr]

    document.clear_parameter(node_id, "value")
    assert document.node(node_id).parameters == {"number_type": "INT"}  # type: ignore[union-attr]

    # Clearing a parameter that was never stored is a no-op: no new revision,
    # no node rewrite, so undo state is untouched.
    revision = document.revision
    document.clear_parameter(node_id, "never_stored")
    assert document.revision == revision
    assert document.node(node_id).parameters == {"number_type": "INT"}  # type: ignore[union-attr]


def test_restore_connection_repoints_same_id_like_add_connection() -> None:
    document = GraphDocument()
    source_a = document.add_node(
        "synmachine.utility.number", implementation_version=2, node_id=NODE_A
    )
    source_b = document.add_node(
        "synmachine.utility.number", implementation_version=2, node_id=NODE_B
    )
    destination = document.add_node("synmachine.utility.math", node_id=NODE_C)
    document.add_connection(source_a, "value", destination, "a", connection_id=CONNECTION)
    repointed = ConnectionModel(CONNECTION, source_b, "value", destination, "a")

    # The undo adapter may restore a repointed connection whose ID is still
    # held by the pre-repoint value: same destination, same rules as
    # add_connection, so redo cannot die on a collision the authoring path
    # allows.
    document.restore_connection(repointed, replace_existing_input=True)

    assert document.connection(CONNECTION) == repointed
    assert document.incoming_connection(destination, "a") == repointed


def test_restore_connection_rejects_same_id_collision_when_destination_differs() -> None:
    document = GraphDocument()
    source_a = document.add_node(
        "synmachine.utility.number", implementation_version=2, node_id=NODE_A
    )
    source_b = document.add_node(
        "synmachine.utility.number", implementation_version=2, node_id=NODE_B
    )
    destination = document.add_node("synmachine.utility.math", node_id=NODE_C)
    document.add_connection(source_a, "value", destination, "a", connection_id=CONNECTION)
    conflicting = ConnectionModel(CONNECTION, source_b, "value", destination, "b")
    before = document.snapshot()

    with pytest.raises(ValueError, match="Connection already exists"):
        document.restore_connection(conflicting, replace_existing_input=True)
    with pytest.raises(ValueError, match="Connection already exists"):
        document.restore_connection(conflicting)

    assert document.snapshot() == before


def test_from_snapshot_rejects_duplicate_identifiers() -> None:
    node_a = NodeModel(NODE_A, "synmachine.utility.number", 1)
    node_b = NodeModel(NODE_B, "synmachine.utility.math", 1)
    connection = ConnectionModel(CONNECTION, NODE_A, "value", NODE_B, "a")

    def snapshot(
        nodes: tuple[NodeModel, ...] = (node_a, node_b),
        connections: tuple[ConnectionModel, ...] = (connection,),
    ) -> GraphSnapshot:
        return GraphSnapshot(
            document_id=UUID("00000000-0000-0000-0000-00000000000d"),
            revision=1,
            nodes=nodes,
            connections=connections,
            document_settings={},
            groups=(),
        )

    with pytest.raises(ValueError, match="duplicate node id"):
        GraphDocument.from_snapshot(snapshot(nodes=(node_a, node_a)))
    with pytest.raises(ValueError, match="duplicate connection id"):
        GraphDocument.from_snapshot(snapshot(connections=(connection, connection)))

    # A well-formed snapshot still restores exactly.
    document = GraphDocument.from_snapshot(snapshot())
    assert document.node(NODE_A) == node_a
    assert document.connection(CONNECTION) == connection
