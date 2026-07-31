"""Validated graph JSON loading, deterministic serialization, and atomic file I/O."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast
from uuid import UUID

from synesthesia_machine.contracts import ColorValue
from synesthesia_machine.graph import ConnectionModel, GraphSnapshot, LiteralValue, NodeModel
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.nodes.input import LOAD_VIDEO_TYPE_ID
from synesthesia_machine.persistence.schemas import (
    GRAPH_SCHEMA_VERSION,
    ConnectionSchemaV1,
    GraphSchemaV1,
    JsonValue,
    NodeSchemaV1,
    migrate_graph_data,
)
from synesthesia_machine.version import __version__


@dataclass(frozen=True, slots=True)
class GraphPersistenceError(ValueError):
    code: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        location = f" at {self.path}" if self.path is not None else ""
        return f"{self.code}{location}: {self.message}"


def graph_to_data(snapshot: GraphSnapshot) -> GraphSchemaV1:
    """Convert an immutable graph snapshot to the current persisted schema."""

    nodes = [_node_to_data(node) for node in sorted(snapshot.nodes, key=lambda item: str(item.id))]
    connections = [
        _connection_to_data(connection)
        for connection in sorted(snapshot.connections, key=lambda item: str(item.id))
    ]
    return GraphSchemaV1(
        schema_version=GRAPH_SCHEMA_VERSION,
        application_version=__version__,
        document_id=str(snapshot.document_id),
        nodes=nodes,
        connections=connections,
        groups=[],
        document_settings=_literal_mapping_to_data(snapshot.document_settings),
        ui_state={},
    )


def graph_to_json(snapshot: GraphSnapshot, *, indent: int = 2) -> str:
    """Serialize a graph deterministically with a final newline."""

    return (
        json.dumps(
            graph_to_data(snapshot),
            allow_nan=False,
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )
        + "\n"
    )


def graph_from_data(data: Mapping[str, object], registry: NodeRegistry) -> GraphSnapshot:
    """Migrate and validate graph data before constructing domain models."""

    try:
        migrated = migrate_graph_data(data)
    except ValueError as error:
        raise GraphPersistenceError("migration_failed", str(error)) from error
    _require_exact_keys(
        migrated,
        {
            "schema_version",
            "application_version",
            "document_id",
            "nodes",
            "connections",
            "groups",
            "document_settings",
            "ui_state",
        },
        "$",
    )
    _expect_int(migrated["schema_version"], "$.schema_version", minimum=1)
    _expect_str(migrated["application_version"], "$.application_version")
    document_id = _expect_uuid(migrated["document_id"], "$.document_id")
    _validate_empty_list(migrated["groups"], "$.groups")
    _validate_empty_object(migrated["ui_state"], "$.ui_state")
    settings = _expect_literal_mapping(migrated["document_settings"], "$.document_settings")

    raw_nodes = _expect_list(migrated["nodes"], "$.nodes")
    nodes = tuple(_node_from_data(value, registry, index) for index, value in enumerate(raw_nodes))
    _ensure_unique_ids((node.id for node in nodes), "node", "$.nodes")

    raw_connections = _expect_list(migrated["connections"], "$.connections")
    connections = tuple(
        _connection_from_data(value, index) for index, value in enumerate(raw_connections)
    )
    _ensure_unique_ids((connection.id for connection in connections), "connection", "$.connections")
    return GraphSnapshot(
        document_id=document_id,
        revision=0,
        nodes=tuple(sorted(nodes, key=lambda item: str(item.id))),
        connections=tuple(sorted(connections, key=lambda item: str(item.id))),
        document_settings=settings,
    )


def graph_from_json(text: str, registry: NodeRegistry) -> GraphSnapshot:
    """Parse, migrate, and validate a graph JSON string."""

    try:
        value = cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise GraphPersistenceError(
            "invalid_json", error.msg, f"line {error.lineno}, column {error.colno}"
        ) from error
    data = _expect_object(value, "$")
    return graph_from_data(data, registry)


def save_graph(path: str | Path, snapshot: GraphSnapshot) -> None:
    """Atomically save a graph and retain one backup of the previous file."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = graph_to_json(_with_persisted_media_paths(snapshot, destination.parent))
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        if destination.exists():
            shutil.copy2(destination, _backup_path(destination))
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_graph(path: str | Path, registry: NodeRegistry) -> GraphSnapshot:
    """Load a UTF-8 graph file through the validated schema boundary."""

    source = Path(path).expanduser().resolve()
    snapshot = graph_from_json(source.read_text(encoding="utf-8"), registry)
    return _with_resolved_media_paths(snapshot, source.parent)


def _with_resolved_media_paths(snapshot: GraphSnapshot, graph_directory: Path) -> GraphSnapshot:
    """Resolve persisted relative media paths against the graph file's directory."""

    return replace(
        snapshot,
        nodes=tuple(
            _replace_video_path(node, _resolve_media_path, graph_directory)
            for node in snapshot.nodes
        ),
    )


def _with_persisted_media_paths(snapshot: GraphSnapshot, graph_directory: Path) -> GraphSnapshot:
    """Prefer graph-relative media paths for files contained by the graph directory."""

    return replace(
        snapshot,
        nodes=tuple(
            _replace_video_path(node, _persisted_media_path, graph_directory)
            for node in snapshot.nodes
        ),
    )


def _replace_video_path(
    node: NodeModel,
    transform: Callable[[str, Path], str],
    graph_directory: Path,
) -> NodeModel:
    if node.type_id != LOAD_VIDEO_TYPE_ID:
        return node
    value = node.parameters.get("file_path")
    if not isinstance(value, str) or not value:
        return node
    transformed = transform(value, graph_directory)
    if transformed == value:
        return node
    parameters = dict(node.parameters)
    parameters["file_path"] = transformed
    return replace(node, parameters=parameters)


def _resolve_media_path(value: str, graph_directory: Path) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = graph_directory / candidate
    return str(candidate.resolve())


def _persisted_media_path(value: str, graph_directory: Path) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return str(candidate)
    resolved = candidate.resolve()
    try:
        return str(resolved.relative_to(graph_directory))
    except ValueError:
        return str(resolved)


def _node_to_data(node: NodeModel) -> NodeSchemaV1:
    return NodeSchemaV1(
        id=str(node.id),
        type_id=node.type_id,
        implementation_version=node.implementation_version,
        position=[node.position[0], node.position[1]],
        size=None if node.size is None else [node.size[0], node.size[1]],
        parameters=_literal_mapping_to_data(node.parameters),
        ui_state=_literal_mapping_to_data(node.ui_state),
        user_label=node.user_label,
        collapsed=node.collapsed,
    )


def _connection_to_data(connection: ConnectionModel) -> ConnectionSchemaV1:
    return ConnectionSchemaV1(
        id=str(connection.id),
        source_node_id=str(connection.source_node_id),
        source_port_id=connection.source_port_id,
        destination_node_id=str(connection.destination_node_id),
        destination_port_id=connection.destination_port_id,
    )


def _node_from_data(value: object, registry: NodeRegistry, index: int) -> NodeModel:
    path = f"$.nodes[{index}]"
    data = _expect_object(value, path)
    _require_exact_keys(
        data,
        {
            "id",
            "type_id",
            "implementation_version",
            "position",
            "size",
            "parameters",
            "ui_state",
            "user_label",
            "collapsed",
        },
        path,
    )
    type_id = _expect_str(data["type_id"], f"{path}.type_id")
    definition = registry.get(type_id)
    if definition is None:
        raise GraphPersistenceError("unknown_node_type", f"Unknown node type {type_id!r}", path)
    version = _expect_int(
        data["implementation_version"], f"{path}.implementation_version", minimum=1
    )
    if version != definition.implementation_version:
        raise GraphPersistenceError(
            "unsupported_node_version",
            f"{type_id!r} version {version} is not supported; expected "
            f"{definition.implementation_version}",
            path,
        )
    position = _expect_pair(data["position"], f"{path}.position")
    raw_size = data["size"]
    size = None if raw_size is None else _expect_pair(raw_size, f"{path}.size")
    raw_label = data["user_label"]
    if raw_label is not None and not isinstance(raw_label, str):
        raise GraphPersistenceError("invalid_type", "Expected string or null", f"{path}.user_label")
    return NodeModel(
        id=_expect_uuid(data["id"], f"{path}.id"),
        type_id=type_id,
        implementation_version=version,
        parameters=_expect_literal_mapping(data["parameters"], f"{path}.parameters"),
        position=position,
        size=size,
        user_label=raw_label,
        collapsed=_expect_bool(data["collapsed"], f"{path}.collapsed"),
        ui_state=_expect_literal_mapping(data["ui_state"], f"{path}.ui_state"),
    )


def _connection_from_data(value: object, index: int) -> ConnectionModel:
    path = f"$.connections[{index}]"
    data = _expect_object(value, path)
    _require_exact_keys(
        data,
        {
            "id",
            "source_node_id",
            "source_port_id",
            "destination_node_id",
            "destination_port_id",
        },
        path,
    )
    return ConnectionModel(
        id=_expect_uuid(data["id"], f"{path}.id"),
        source_node_id=_expect_uuid(data["source_node_id"], f"{path}.source_node_id"),
        source_port_id=_expect_str(data["source_port_id"], f"{path}.source_port_id"),
        destination_node_id=_expect_uuid(
            data["destination_node_id"], f"{path}.destination_node_id"
        ),
        destination_port_id=_expect_str(data["destination_port_id"], f"{path}.destination_port_id"),
    )


def _expect_object(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GraphPersistenceError("invalid_type", "Expected JSON object", path)
    raw = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            raise GraphPersistenceError("invalid_type", "Expected string object keys", path)
        result[key] = item
    return result


def _expect_list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise GraphPersistenceError("invalid_type", "Expected JSON array", path)
    return cast(list[object], value)


def _expect_str(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise GraphPersistenceError("invalid_type", "Expected string", path)
    return value


def _expect_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise GraphPersistenceError("invalid_type", "Expected boolean", path)
    return value


def _expect_int(value: object, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GraphPersistenceError(
            "invalid_type", f"Expected integer greater than or equal to {minimum}", path
        )
    return value


def _expect_uuid(value: object, path: str) -> UUID:
    text = _expect_str(value, path)
    try:
        return UUID(text)
    except ValueError as error:
        raise GraphPersistenceError("invalid_uuid", f"Invalid UUID {text!r}", path) from error


def _expect_pair(value: object, path: str) -> tuple[float, float]:
    items = _expect_list(value, path)
    if len(items) != 2:
        raise GraphPersistenceError("invalid_shape", "Expected exactly two numbers", path)
    return (_expect_number(items[0], f"{path}[0]"), _expect_number(items[1], f"{path}[1]"))


def _expect_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphPersistenceError("invalid_type", "Expected number", path)
    result = float(value)
    if not math.isfinite(result):
        raise GraphPersistenceError("invalid_number", "Expected finite number", path)
    return result


def _expect_literal_mapping(value: object, path: str) -> dict[str, LiteralValue]:
    data = _expect_object(value, path)
    return {key: _expect_literal(item, f"{path}.{key}") for key, item in data.items()}


def _expect_literal(value: object, path: str) -> LiteralValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, dict):
        data = _expect_object(cast(object, value), path)
        _require_exact_keys(data, {"$type", "r", "g", "b", "a"}, path)
        if _expect_str(data["$type"], f"{path}.$type") != "COLOR":
            raise GraphPersistenceError(
                "invalid_literal", "Unknown structured literal type", f"{path}.$type"
            )
        try:
            return ColorValue(
                _expect_number(data["r"], f"{path}.r"),
                _expect_number(data["g"], f"{path}.g"),
                _expect_number(data["b"], f"{path}.b"),
                _expect_number(data["a"], f"{path}.a"),
            )
        except ValueError as error:
            raise GraphPersistenceError("invalid_literal", str(error), path) from error
    raise GraphPersistenceError("invalid_literal", "Expected a finite JSON literal", path)


def _literal_mapping_to_data(values: Mapping[str, LiteralValue]) -> dict[str, JsonValue]:
    return {key: _literal_to_data(value) for key, value in sorted(values.items())}


def _literal_to_data(value: LiteralValue) -> JsonValue:
    if isinstance(value, ColorValue):
        return {"$type": "COLOR", "r": value.r, "g": value.g, "b": value.b, "a": value.a}
    return value


def _require_exact_keys(data: Mapping[str, object], expected: set[str], path: str) -> None:
    missing = sorted(expected - set(data))
    unknown = sorted(set(data) - expected)
    if missing:
        raise GraphPersistenceError("missing_field", f"Missing fields: {', '.join(missing)}", path)
    if unknown:
        raise GraphPersistenceError("unknown_field", f"Unknown fields: {', '.join(unknown)}", path)


def _validate_empty_list(value: object, path: str) -> None:
    if not isinstance(value, list):
        raise GraphPersistenceError("invalid_type", "Expected JSON array", path)
    if value:
        raise GraphPersistenceError(
            "unsupported_content", "This reserved field must be empty in schema version 1", path
        )


def _validate_empty_object(value: object, path: str) -> None:
    if not isinstance(value, dict):
        raise GraphPersistenceError("invalid_type", "Expected JSON object", path)
    if value:
        raise GraphPersistenceError(
            "unsupported_content", "This reserved field must be empty in schema version 1", path
        )


def _ensure_unique_ids(values: Iterable[UUID], kind: str, path: str) -> None:
    identifiers = tuple(values)
    if len(set(identifiers)) != len(identifiers):
        raise GraphPersistenceError("duplicate_id", f"Duplicate {kind} ID", path)


def _backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")
