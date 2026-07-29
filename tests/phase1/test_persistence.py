"""Versioned graph JSON and migration tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest

from synesthesia_machine.contracts import ColorValue
from synesthesia_machine.graph import GraphCompiler, GraphDocument
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
from tests.phase1.helpers import frame_context

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
    assert migrated["schema_version"] == 1
    migrated_nodes = migrated["nodes"]
    assert isinstance(migrated_nodes, list)
    migrated_node = migrated_nodes[0]
    assert isinstance(migrated_node, dict)
    assert migrated_node["implementation_version"] == 1
    loaded = graph_from_data(prototype, create_utility_registry())
    assert loaded.nodes[0].parameters["float_value"] == 7.0


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
