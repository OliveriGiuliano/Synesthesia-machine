"""Versioned graph JSON and migration tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from tests.support.graph_factories import frame_context

import synesthesia_machine.persistence.graph_io as graph_io
from synesthesia_machine.contracts import ColorValue, NumericMatrix
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes.composition import create_builtin_registry
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import (
    GraphPersistenceError,
    graph_from_data,
    graph_from_json,
    graph_to_data,
    graph_to_json,
    load_graph,
    migrate_graph_data,
    save_graph,
)
from synesthesia_machine.runtime import PortKey, Scheduler

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000100")
NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")


def test_json_round_trip_is_deterministic_and_preserves_semantics() -> None:
    document = GraphDocument(document_id=DOCUMENT_ID)
    source = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 2.5},
        position=(12.5, 30.0),
    )
    passthrough = document.add_node("synmachine.utility.pass_through", node_id=NODE_A)
    document.add_connection(source, "value", passthrough, "value")
    document.set_document_setting("accent", ColorValue(0.1, 0.2, 0.3, 0.4))
    snapshot = document.snapshot()

    text = graph_to_json(snapshot)
    loaded = graph_from_json(text, create_utility_registry())

    assert graph_to_json(loaded) == text
    assert loaded.document_id == snapshot.document_id
    assert loaded.nodes == snapshot.nodes
    assert loaded.connections == snapshot.connections
    assert loaded.document_settings == snapshot.document_settings


def test_numeric_matrix_round_trips_as_nested_json_arrays() -> None:
    registry = create_builtin_registry()
    document = GraphDocument(document_id=DOCUMENT_ID)
    kernel = NumericMatrix(((0.0, 1.0, 0.0), (1.0, -4.0, 1.0), (0.0, 1.0, 0.0)))
    document.add_node(
        "synmachine.image.convolve",
        node_id=NODE_A,
        parameters={"kernel": kernel},
    )

    data = graph_to_data(document.snapshot())
    assert data["nodes"][0]["parameters"]["kernel"] == [
        [0.0, 1.0, 0.0],
        [1.0, -4.0, 1.0],
        [0.0, 1.0, 0.0],
    ]
    loaded = graph_from_json(graph_to_json(document.snapshot()), registry)
    assert loaded.nodes[0].parameters["kernel"] == kernel


@pytest.mark.parametrize(
    ("matrix", "code", "path_fragment"),
    [
        ([], "invalid_literal", "parameters.kernel"),
        ([[1.0], [2.0, 3.0]], "invalid_literal", "parameters.kernel"),
        ([[1.0, True]], "invalid_type", "parameters.kernel[0][1]"),
        ([[1.0, float("inf")]], "invalid_number", "parameters.kernel[0][1]"),
        ([[10**1000]], "invalid_number", "parameters.kernel[0][0]"),
        ([1.0, 2.0], "invalid_type", "parameters.kernel[0]"),
    ],
)
def test_loader_rejects_malformed_numeric_matrices(
    matrix: object,
    code: str,
    path_fragment: str,
) -> None:
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("synmachine.image.convolve", node_id=NODE_A)
    data = graph_to_data(document.snapshot())
    data["nodes"][0]["parameters"]["kernel"] = matrix  # type: ignore[typeddict-item]

    with pytest.raises(GraphPersistenceError) as captured:
        graph_from_data(data, create_builtin_registry())
    assert captured.value.code == code
    assert captured.value.path is not None and path_fragment in captured.value.path


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("schema_version", 999, "migration_failed"),
        ("node_type", "unknown.node", "unknown_node_type"),
        ("node_version", 2, "unsupported_node_version"),
    ],
)
def test_loader_rejects_unknown_schema_type_and_node_version(
    field: str, value: int | str, code: str
) -> None:
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("synmachine.utility.number", node_id=NODE_A)
    data = graph_to_data(document.snapshot())
    if field == "schema_version":
        data["schema_version"] = int(value)
    elif field == "node_type":
        data["nodes"][0]["type_id"] = str(value)
    else:
        data["nodes"][0]["implementation_version"] = int(value)

    with pytest.raises(GraphPersistenceError) as captured:
        graph_from_data(data, create_utility_registry())
    assert captured.value.code == code


def test_v0_migration_is_pure_and_sequential() -> None:
    prototype = {
        "document_id": str(DOCUMENT_ID),
        "nodes": [
            {
                "id": str(NODE_A),
                "type_id": "synmachine.utility.number",
                "version": 1,
                "parameters": {"float_value": 7.0},
            }
        ],
        "connections": [],
    }
    original = deepcopy(prototype)
    migrated = migrate_graph_data(prototype)

    assert prototype == original
    assert migrated["schema_version"] == 2
    migrated_nodes = migrated["nodes"]
    assert isinstance(migrated_nodes, list)
    migrated_node = migrated_nodes[0]
    assert isinstance(migrated_node, dict)
    assert migrated_node["implementation_version"] == 1
    loaded = graph_from_data(prototype, create_utility_registry())
    assert loaded.nodes[0].parameters["float_value"] == 7.0


def test_v1_connection_payload_migrates_to_v2_and_round_trips() -> None:
    registry = create_utility_registry()
    document = GraphDocument(document_id=DOCUMENT_ID)
    source = document.add_node("synmachine.utility.number", node_id=NODE_B)
    target = document.add_node("synmachine.utility.pass_through", node_id=NODE_A)
    document.add_connection(source, "value", target, "value")

    # Reconstruct a schema v1 payload: the canonical v2 JSON with the version
    # marker lowered and each per-connection ui_state field removed.
    payload = cast("dict[str, object]", json.loads(graph_to_json(document.snapshot())))
    v1_payload = cast("dict[str, object]", deepcopy(payload))
    v1_payload["schema_version"] = 1
    for raw_connection in cast("list[object]", v1_payload["connections"]):
        cast("dict[str, object]", raw_connection).pop("ui_state")
    original = deepcopy(v1_payload)

    migrated = migrate_graph_data(v1_payload)

    assert v1_payload == original
    assert migrated["schema_version"] == 2
    for raw_connection in cast("list[object]", migrated["connections"]):
        assert cast("dict[str, object]", raw_connection)["ui_state"] == {}

    # The backfilled ui_state must survive a full serialize/parse round-trip.
    loaded = graph_from_data(migrated, registry)
    assert loaded.connections[0].ui_state == {}
    reparsed = graph_from_json(graph_to_json(loaded), registry)
    assert reparsed.connections[0].ui_state == {}


def test_atomic_save_load_and_backup(tmp_path: Path) -> None:
    document = GraphDocument(document_id=DOCUMENT_ID)
    number = document.add_node("synmachine.utility.number", node_id=NODE_A)
    path = tmp_path / "graph.synmachine.json"
    save_graph(path, document.snapshot())
    first_content = path.read_text(encoding="utf-8")

    document.set_parameter(number, "float_value", 9.0)
    save_graph(path, document.snapshot())

    assert path.with_name(f"{path.name}.bak").read_text(encoding="utf-8") == first_content
    assert load_graph(path, create_utility_registry()).nodes[0].parameters["float_value"] == 9.0


def test_graph_json_and_file_reads_are_bounded_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_io, "MAX_GRAPH_JSON_BYTES", 32)
    oversized = " " * 33

    with pytest.raises(GraphPersistenceError) as text_error:
        graph_from_json(oversized, create_utility_registry())
    assert text_error.value.code == "graph_too_large"

    path = tmp_path / "oversized.synmachine.json"
    path.write_bytes(oversized.encode("utf-8"))
    with pytest.raises(GraphPersistenceError) as file_error:
        load_graph(path, create_utility_registry())
    assert file_error.value.code == "graph_too_large"


def test_graph_loader_rejects_excess_cardinality_and_dangling_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = create_utility_registry()
    document = GraphDocument(document_id=DOCUMENT_ID)
    source = document.add_node("synmachine.utility.number", node_id=NODE_A)
    target = document.add_node("synmachine.utility.pass_through", node_id=NODE_B)
    document.add_connection(source, "value", target, "value")
    data = graph_to_data(document.snapshot())

    monkeypatch.setattr(graph_io, "MAX_GRAPH_NODES", 1)
    with pytest.raises(GraphPersistenceError) as too_many:
        graph_from_data(data, registry)
    assert too_many.value.code == "too_many_items"

    monkeypatch.setattr(graph_io, "MAX_GRAPH_NODES", 10_000)
    data["connections"][0]["destination_node_id"] = str(UUID(int=999))
    with pytest.raises(GraphPersistenceError) as dangling:
        graph_from_data(data, registry)
    assert dangling.value.code == "dangling_connection"


def test_serialized_graph_loads_compiles_and_executes() -> None:
    registry = create_utility_registry()
    document = GraphDocument(document_id=DOCUMENT_ID)
    first = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "INT", "int_value": 4},
    )
    second = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 1.5},
    )
    math_id = UUID("00000000-0000-0000-0000-00000000000c")
    math_node = document.add_node(
        "synmachine.utility.math", node_id=math_id, parameters={"operation": "ADD"}
    )
    document.add_connection(first, "value", math_node, "a")
    document.add_connection(second, "value", math_node, "b")

    loaded = graph_from_json(graph_to_json(document.snapshot()), registry)
    compilation = GraphCompiler(registry).compile(loaded)
    assert compilation.plan is not None
    result = Scheduler(compilation.plan).execute_tick(frame_context(clock_id=DOCUMENT_ID))

    assert result.errors == ()
    assert result.values[PortKey(math_node, "value")] == 5.5
