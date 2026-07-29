"""Pure clipboard fragment, serialization, and remapping tests."""

from collections.abc import Iterator
from uuid import UUID

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import (
    copy_fragment,
    fragment_from_json,
    fragment_to_json,
    remap_fragment,
)

NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")
NODE_C = UUID("00000000-0000-0000-0000-00000000000c")
NEW_A = UUID("10000000-0000-0000-0000-00000000000a")
NEW_B = UUID("10000000-0000-0000-0000-00000000000b")
NEW_CONNECTION = UUID("10000000-0000-0000-0000-00000000001a")


def test_copy_excludes_external_edges_and_json_round_trips() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A, position=(1.0, 2.0))
    second = document.add_node("synmachine.utility.math", node_id=NODE_B, position=(3.0, 4.0))
    external = document.add_node("synmachine.utility.pass_through", node_id=NODE_C)
    document.add_connection(first, "value", second, "a")
    document.add_connection(second, "value", external, "value")

    fragment = copy_fragment(document.snapshot(), {first, second})

    assert {node.id for node in fragment.nodes} == {NODE_A, NODE_B}
    assert len(fragment.connections) == 1
    assert fragment_from_json(fragment_to_json(fragment)) == fragment


def test_remap_uses_fresh_ids_preserves_internal_edge_and_offsets_positions() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A, position=(1.0, 2.0))
    second = document.add_node("synmachine.utility.math", node_id=NODE_B, position=(3.0, 4.0))
    document.add_connection(first, "value", second, "a")
    identifiers: Iterator[UUID] = iter((NEW_A, NEW_B, NEW_CONNECTION))

    remapped = remap_fragment(
        copy_fragment(document.snapshot(), {first, second}),
        offset=(32.0, 48.0),
        id_factory=lambda: next(identifiers),
    )

    assert tuple(node.id for node in remapped.nodes) == (NEW_A, NEW_B)
    assert tuple(node.position for node in remapped.nodes) == ((33.0, 50.0), (35.0, 52.0))
    assert remapped.connections[0].id == NEW_CONNECTION
    assert remapped.connections[0].source_node_id == NEW_A
    assert remapped.connections[0].destination_node_id == NEW_B
