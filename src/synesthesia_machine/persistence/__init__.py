"""Public schema-versioned graph persistence facade."""

from synesthesia_machine.persistence.clipboard import (
    CLIPBOARD_FRAGMENT_VERSION,
    ClipboardFragment,
    copy_fragment,
    fragment_from_json,
    fragment_to_json,
    remap_fragment,
)
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
    GroupSchemaV1,
    JsonObject,
    JsonValue,
    NodeSchemaV1,
    migrate_graph_data,
    migrate_v0_to_v1,
)

__all__ = [
    "CLIPBOARD_FRAGMENT_VERSION",
    "GRAPH_SCHEMA_VERSION",
    "ClipboardFragment",
    "ConnectionSchemaV1",
    "GraphPersistenceError",
    "GraphSchemaV1",
    "GroupSchemaV1",
    "JsonObject",
    "JsonValue",
    "NodeSchemaV1",
    "copy_fragment",
    "fragment_from_json",
    "fragment_to_json",
    "graph_from_data",
    "graph_from_json",
    "graph_to_data",
    "graph_to_json",
    "load_graph",
    "migrate_graph_data",
    "migrate_v0_to_v1",
    "remap_fragment",
    "save_graph",
]
