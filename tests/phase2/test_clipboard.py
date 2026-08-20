"""Pure clipboard fragment, serialization, and remapping tests."""

import json
from collections.abc import Iterator
from uuid import UUID

import pytest

import synesthesia_machine.persistence.clipboard as clipboard_io
from synesthesia_machine.contracts import NumericMatrix
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import (
    CLIPBOARD_FRAGMENT_VERSION,
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
    internal = document.add_connection(first, "value", second, "a")
    document.set_connection_ui_state(internal, "preview_visible", False)
    document.add_connection(second, "value", external, "value")

    fragment = copy_fragment(document.snapshot(), {first, second})

    assert {node.id for node in fragment.nodes} == {NODE_A, NODE_B}
    assert len(fragment.connections) == 1
    assert fragment.connections[0].ui_state == {"preview_visible": False}
    assert fragment_from_json(fragment_to_json(fragment)) == fragment


def test_remap_uses_fresh_ids_preserves_internal_edge_and_offsets_positions() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A, position=(1.0, 2.0))
    second = document.add_node("synmachine.utility.math", node_id=NODE_B, position=(3.0, 4.0))
    connection_id = document.add_connection(first, "value", second, "a")
    document.set_connection_ui_state(connection_id, "preview_visible", False)
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
    assert remapped.connections[0].ui_state == {"preview_visible": False}


def test_version_one_fragment_loads_with_default_connection_ui_state() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A)
    second = document.add_node("synmachine.utility.math", node_id=NODE_B)
    document.add_connection(first, "value", second, "a")
    raw = json.loads(fragment_to_json(copy_fragment(document.snapshot(), {first, second})))
    raw["fragment_version"] = 1
    raw["connections"][0].pop("ui_state")

    fragment = fragment_from_json(json.dumps(raw))

    assert fragment.version == CLIPBOARD_FRAGMENT_VERSION == 2
    assert fragment.connections[0].ui_state == {}


def test_clipboard_numeric_matrix_round_trips_as_nested_arrays() -> None:
    document = GraphDocument()
    kernel = NumericMatrix(((1.0, 2.0, 1.0), (0.0, 0.0, 0.0), (-1.0, -2.0, -1.0)))
    node_id = document.add_node(
        "synmachine.image.convolve",
        node_id=NODE_A,
        parameters={"kernel": kernel},
    )
    fragment = copy_fragment(document.snapshot(), {node_id})

    text = fragment_to_json(fragment)
    raw = json.loads(text)
    assert raw["nodes"][0]["parameters"]["kernel"] == [
        [1.0, 2.0, 1.0],
        [0.0, 0.0, 0.0],
        [-1.0, -2.0, -1.0],
    ]
    assert fragment_from_json(text).nodes[0].parameters["kernel"] == kernel


@pytest.mark.parametrize(
    ("matrix", "message"),
    [
        ([], "at least one row"),
        ([[1.0], [2.0, 3.0]], "equal lengths"),
        ([[1.0, True]], "must be a number"),
        ([[10**1000]], "must be finite"),
        ([1.0, 2.0], "must be an array"),
    ],
)
def test_clipboard_rejects_malformed_numeric_matrices(matrix: object, message: str) -> None:
    document = GraphDocument()
    node_id = document.add_node("synmachine.image.convolve", node_id=NODE_A)
    raw = json.loads(fragment_to_json(copy_fragment(document.snapshot(), {node_id})))
    raw["nodes"][0]["parameters"]["kernel"] = matrix

    with pytest.raises(ValueError, match=message):
        fragment_from_json(json.dumps(raw))


def test_clipboard_input_has_byte_and_cardinality_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clipboard_io, "MAX_CLIPBOARD_JSON_BYTES", 16)
    with pytest.raises(ValueError, match="exceeds"):
        fragment_from_json(" " * 17)

    monkeypatch.setattr(clipboard_io, "MAX_CLIPBOARD_JSON_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(clipboard_io, "MAX_CLIPBOARD_NODES", 0)
    document = GraphDocument()
    node_id = document.add_node("synmachine.utility.number", node_id=NODE_A)
    text = fragment_to_json(copy_fragment(document.snapshot(), {node_id}))
    with pytest.raises(ValueError, match="more than 0 nodes"):
        fragment_from_json(text)
