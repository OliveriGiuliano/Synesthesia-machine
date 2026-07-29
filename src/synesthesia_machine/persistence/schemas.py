"""Versioned JSON-compatible graph schemas and pure migrations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import TypedDict, cast

from synesthesia_machine.version import __version__

GRAPH_SCHEMA_VERSION = 1

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type GraphMigration = Callable[[JsonObject], JsonObject]


class NodeSchemaV1(TypedDict):
    id: str
    type_id: str
    implementation_version: int
    position: list[float]
    size: list[float] | None
    parameters: dict[str, JsonValue]
    ui_state: dict[str, JsonValue]
    user_label: str | None
    collapsed: bool


class ConnectionSchemaV1(TypedDict):
    id: str
    source_node_id: str
    source_port_id: str
    destination_node_id: str
    destination_port_id: str


class GraphSchemaV1(TypedDict):
    schema_version: int
    application_version: str
    document_id: str
    nodes: list[NodeSchemaV1]
    connections: list[ConnectionSchemaV1]
    groups: list[JsonObject]
    document_settings: dict[str, JsonValue]
    ui_state: dict[str, JsonValue]


def migrate_graph_data(data: Mapping[str, object]) -> JsonObject:
    """Return current schema data without mutating the caller's dictionary."""

    migrated = _copy_json_object(data)
    version = _schema_version(migrated)
    if version > GRAPH_SCHEMA_VERSION:
        msg = (
            f"Graph schema version {version} is newer than supported version {GRAPH_SCHEMA_VERSION}"
        )
        raise ValueError(msg)
    while version < GRAPH_SCHEMA_VERSION:
        migration = _MIGRATIONS.get(version)
        if migration is None:
            msg = f"No graph migration is registered for schema version {version}"
            raise ValueError(msg)
        migrated = migration(migrated)
        next_version = _schema_version(migrated)
        if next_version != version + 1:
            msg = f"Migration {version} must produce schema version {version + 1}"
            raise ValueError(msg)
        version = next_version
    return migrated


def migrate_v0_to_v1(data: JsonObject) -> JsonObject:
    """Migrate the pre-versioned prototype shape to the first public schema."""

    migrated = deepcopy(data)
    migrated["schema_version"] = 1
    migrated.setdefault("application_version", __version__)
    migrated.setdefault("groups", [])
    migrated.setdefault("document_settings", {})
    migrated.setdefault("ui_state", {})

    nodes = migrated.get("nodes")
    if isinstance(nodes, list):
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                continue
            node = cast(dict[str, JsonValue], raw_node)
            if "implementation_version" not in node and "version" in node:
                node["implementation_version"] = node.pop("version")
            node.setdefault("position", [0.0, 0.0])
            node.setdefault("size", None)
            node.setdefault("parameters", {})
            node.setdefault("ui_state", {})
            node.setdefault("user_label", None)
            node.setdefault("collapsed", False)
    return migrated


def _schema_version(data: JsonObject) -> int:
    value = data.get("schema_version", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = "schema_version must be a non-negative integer"
        raise ValueError(msg)
    return value


def _copy_json_object(data: Mapping[str, object]) -> JsonObject:
    copied: JsonObject = {}
    for key, value in data.items():
        copied[key] = _copy_json_value(value, key)
    return copied


def _copy_json_value(value: object, path: str) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_copy_json_value(item, f"{path}[]") for item in items]
    if isinstance(value, Mapping):
        result: JsonObject = {}
        mapping = cast(Mapping[object, object], value)
        for key, item in mapping.items():
            if not isinstance(key, str):
                msg = f"{path} contains a non-string object key"
                raise ValueError(msg)
            result[key] = _copy_json_value(item, f"{path}.{key}")
        return result
    msg = f"{path} contains a value that is not JSON-compatible"
    raise ValueError(msg)


_MIGRATIONS: dict[int, GraphMigration] = {0: migrate_v0_to_v1}
