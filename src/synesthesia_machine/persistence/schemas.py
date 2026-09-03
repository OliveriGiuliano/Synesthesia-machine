"""Versioned JSON-compatible graph schemas and pure migrations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import TypedDict, cast

from synesthesia_machine.version import __version__

GRAPH_SCHEMA_VERSION = 4

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
    # Per-connection UI state (the live-preview visibility flag). Absent in v1
    # payloads; the v1 -> v2 migration backfills it as the empty object.
    ui_state: dict[str, JsonValue]


class GroupSchemaV1(TypedDict):
    id: str
    kind: str
    title: str
    text: str
    position: list[float]
    size: list[float]
    color: str


class GraphSchemaV1(TypedDict):
    schema_version: int
    application_version: str
    document_id: str
    nodes: list[NodeSchemaV1]
    connections: list[ConnectionSchemaV1]
    groups: list[GroupSchemaV1]
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


def migrate_v1_to_v2(data: JsonObject) -> JsonObject:
    """Migrate v1 connections so each carries per-connection UI state."""

    migrated = deepcopy(data)
    migrated["schema_version"] = 2
    connections = migrated.get("connections")
    if isinstance(connections, list):
        for raw_connection in connections:
            if not isinstance(raw_connection, dict):
                continue
            connection = cast(dict[str, JsonValue], raw_connection)
            connection.setdefault("ui_state", {})
    return migrated


def migrate_v2_to_v3(data: JsonObject) -> JsonObject:
    """Point Statistics inputs at their first variadic socket.

    The Statistics node replaced its single ``values`` input with an indexed
    ``values_1``... socket family so one node can combine several connected
    values or a Buffer. Saved connections still target the legacy ``values``
    port, which now means the first socket.
    """

    migrated = deepcopy(data)
    migrated["schema_version"] = 3
    nodes = migrated.get("nodes")
    statistics_ids: set[str] = set()
    if isinstance(nodes, list):
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                continue
            node = cast(dict[str, JsonValue], raw_node)
            if node.get("type_id") == "synmachine.utility.statistics":
                node_id = node.get("id")
                if isinstance(node_id, str):
                    statistics_ids.add(node_id)
    if not statistics_ids:
        return migrated
    connections = migrated.get("connections")
    if not isinstance(connections, list):
        return migrated
    for raw_connection in connections:
        if not isinstance(raw_connection, dict):
            continue
        connection = cast(dict[str, JsonValue], raw_connection)
        destination = connection.get("destination_node_id")
        port = connection.get("destination_port_id")
        if destination in statistics_ids and port == "values":
            connection["destination_port_id"] = "values_1"
    return migrated


def migrate_v3_to_v4(data: JsonObject) -> JsonObject:
    """Drop retired alpha-channel surfaces so saved graphs stay valid.

    Sources never carry an alpha channel, so the node catalogue no longer
    offers a fourth channel: the Opacity node was removed and Separate/
    Combine Channels lost their ``channel_4`` sockets. This migration drops
    Opacity nodes and any connection touching a retired socket so the graph
    loads without unknown-port or unknown-node errors. Parameter rewrites
    (RGBA target, CHANNEL_4 selection) live in the node migrations.
    """

    migrated = deepcopy(data)
    migrated["schema_version"] = 4
    nodes = migrated.get("nodes")
    if not isinstance(nodes, list):
        return migrated
    opacity_ids: set[str] = set()
    separate_ids: set[str] = set()
    combine_ids: set[str] = set()
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node = cast(dict[str, JsonValue], raw_node)
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        type_id = node.get("type_id")
        if type_id == "synmachine.image.opacity":
            opacity_ids.add(node_id)
        elif type_id == "synmachine.image.separate_channels":
            separate_ids.add(node_id)
        elif type_id == "synmachine.image.combine_channels":
            combine_ids.add(node_id)
    if opacity_ids:
        migrated["nodes"] = [
            node for node in nodes if not (isinstance(node, dict) and node.get("id") in opacity_ids)
        ]
    connections = migrated.get("connections")
    if not isinstance(connections, list):
        return migrated
    kept: list[JsonValue] = []
    for raw_connection in connections:
        if not isinstance(raw_connection, dict):
            kept.append(raw_connection)
            continue
        connection = cast(dict[str, JsonValue], raw_connection)
        source = connection.get("source_node_id")
        destination = connection.get("destination_node_id")
        if source in opacity_ids or destination in opacity_ids:
            continue
        if destination in combine_ids and connection.get("destination_port_id") == "channel_4":
            continue
        if source in separate_ids and connection.get("source_port_id") == "channel_4":
            continue
        kept.append(raw_connection)
    migrated["connections"] = kept
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


_MIGRATIONS: dict[int, GraphMigration] = {
    0: migrate_v0_to_v1,
    1: migrate_v1_to_v2,
    2: migrate_v2_to_v3,
    3: migrate_v3_to_v4,
}
