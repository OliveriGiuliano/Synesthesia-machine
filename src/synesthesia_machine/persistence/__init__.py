"""Public schema-versioned graph persistence facade."""

from synesthesia_machine.persistence.graph_io import (
    GraphPersistenceError,
    graph_from_data,
    graph_from_json,
    graph_to_data,
    graph_to_json,
    load_graph,
    save_graph,
)
from synesthesia_machine.persistence.schemas import (
    GRAPH_SCHEMA_VERSION,
    ConnectionSchemaV1,
    GraphSchemaV1,
    JsonObject,
    JsonValue,
    NodeSchemaV1,
    migrate_graph_data,
    migrate_v0_to_v1,
)

__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "ConnectionSchemaV1",
    "GraphPersistenceError",
    "GraphSchemaV1",
    "JsonObject",
    "JsonValue",
    "NodeSchemaV1",
    "graph_from_data",
    "graph_from_json",
    "graph_to_data",
    "graph_to_json",
    "load_graph",
    "migrate_graph_data",
    "migrate_v0_to_v1",
    "save_graph",
]
