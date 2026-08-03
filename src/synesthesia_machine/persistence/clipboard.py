"""Versioned, Qt-free graph clipboard fragments and UUID remapping."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

from synesthesia_machine.contracts import ColorValue, NumericMatrix
from synesthesia_machine.graph import ConnectionModel, GraphSnapshot, LiteralValue, NodeModel

CLIPBOARD_FRAGMENT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ClipboardFragment:
    """A self-contained selection with only its internal edges."""

    nodes: tuple[NodeModel, ...]
    connections: tuple[ConnectionModel, ...]
    version: int = CLIPBOARD_FRAGMENT_VERSION

    def __post_init__(self) -> None:
        if self.version != CLIPBOARD_FRAGMENT_VERSION:
            msg = f"Unsupported clipboard fragment version: {self.version}"
            raise ValueError(msg)
        node_ids = {node.id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            msg = "Clipboard fragment contains duplicate node IDs"
            raise ValueError(msg)
        connection_ids = {connection.id for connection in self.connections}
        if len(connection_ids) != len(self.connections):
            msg = "Clipboard fragment contains duplicate connection IDs"
            raise ValueError(msg)
        if any(
            connection.source_node_id not in node_ids
            or connection.destination_node_id not in node_ids
            for connection in self.connections
        ):
            msg = "Clipboard fragment connections must be internal"
            raise ValueError(msg)


def copy_fragment(snapshot: GraphSnapshot, selected_node_ids: set[UUID]) -> ClipboardFragment:
    """Copy selected nodes and only connections whose two endpoints are selected."""

    selected = frozenset(selected_node_ids)
    nodes = tuple(node for node in snapshot.nodes if node.id in selected)
    present = {node.id for node in nodes}
    connections = tuple(
        connection
        for connection in snapshot.connections
        if connection.source_node_id in present and connection.destination_node_id in present
    )
    return ClipboardFragment(nodes=nodes, connections=connections)


def remap_fragment(
    fragment: ClipboardFragment,
    *,
    offset: tuple[float, float] = (32.0, 32.0),
    id_factory: Callable[[], UUID] = uuid4,
) -> ClipboardFragment:
    """Return a paste-ready fragment with fresh IDs and deterministic position offset."""

    node_ids = {
        node.id: id_factory() for node in sorted(fragment.nodes, key=lambda item: str(item.id))
    }
    nodes = tuple(
        NodeModel(
            id=node_ids[node.id],
            type_id=node.type_id,
            implementation_version=node.implementation_version,
            parameters=node.parameters,
            position=(node.position[0] + offset[0], node.position[1] + offset[1]),
            size=node.size,
            user_label=node.user_label,
            collapsed=node.collapsed,
            ui_state=node.ui_state,
        )
        for node in sorted(fragment.nodes, key=lambda item: str(item.id))
    )
    connections = tuple(
        ConnectionModel(
            id=id_factory(),
            source_node_id=node_ids[connection.source_node_id],
            source_port_id=connection.source_port_id,
            destination_node_id=node_ids[connection.destination_node_id],
            destination_port_id=connection.destination_port_id,
        )
        for connection in sorted(fragment.connections, key=lambda item: str(item.id))
    )
    return ClipboardFragment(nodes=nodes, connections=connections)


def fragment_to_json(fragment: ClipboardFragment) -> str:
    """Serialize a clipboard fragment deterministically."""

    data = {
        "fragment_version": fragment.version,
        "nodes": [
            _node_to_data(node) for node in sorted(fragment.nodes, key=lambda item: str(item.id))
        ],
        "connections": [
            _connection_to_data(connection)
            for connection in sorted(fragment.connections, key=lambda item: str(item.id))
        ],
    }
    return json.dumps(
        data, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def fragment_from_json(text: str) -> ClipboardFragment:
    """Parse and strictly validate the current clipboard fragment format."""

    try:
        value = cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid clipboard JSON: {error.msg}") from error
    data = _object(value, "$")
    if set(data) != {"fragment_version", "nodes", "connections"}:
        msg = "Clipboard fragment fields do not match version 1"
        raise ValueError(msg)
    version = data["fragment_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        msg = "Clipboard fragment version must be an integer"
        raise ValueError(msg)
    raw_nodes = _list(data["nodes"], "$.nodes")
    raw_connections = _list(data["connections"], "$.connections")
    return ClipboardFragment(
        nodes=tuple(_node_from_data(item, index) for index, item in enumerate(raw_nodes)),
        connections=tuple(
            _connection_from_data(item, index) for index, item in enumerate(raw_connections)
        ),
        version=version,
    )


def _node_to_data(node: NodeModel) -> dict[str, object]:
    return {
        "id": str(node.id),
        "type_id": node.type_id,
        "implementation_version": node.implementation_version,
        "parameters": {
            key: _literal_to_data(value) for key, value in sorted(node.parameters.items())
        },
        "position": [node.position[0], node.position[1]],
        "size": None if node.size is None else [node.size[0], node.size[1]],
        "user_label": node.user_label,
        "collapsed": node.collapsed,
        "ui_state": {key: _literal_to_data(value) for key, value in sorted(node.ui_state.items())},
    }


def _connection_to_data(connection: ConnectionModel) -> dict[str, object]:
    return {
        "id": str(connection.id),
        "source_node_id": str(connection.source_node_id),
        "source_port_id": connection.source_port_id,
        "destination_node_id": str(connection.destination_node_id),
        "destination_port_id": connection.destination_port_id,
    }


def _node_from_data(value: object, index: int) -> NodeModel:
    path = f"$.nodes[{index}]"
    data = _object(value, path)
    expected = {
        "id",
        "type_id",
        "implementation_version",
        "parameters",
        "position",
        "size",
        "user_label",
        "collapsed",
        "ui_state",
    }
    if set(data) != expected:
        raise ValueError(f"{path} fields do not match version 1")
    version = data["implementation_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(f"{path}.implementation_version must be an integer")
    label = data["user_label"]
    if label is not None and not isinstance(label, str):
        raise ValueError(f"{path}.user_label must be a string or null")
    collapsed = data["collapsed"]
    if not isinstance(collapsed, bool):
        raise ValueError(f"{path}.collapsed must be a boolean")
    raw_size = data["size"]
    return NodeModel(
        id=_uuid(data["id"], f"{path}.id"),
        type_id=_string(data["type_id"], f"{path}.type_id"),
        implementation_version=version,
        parameters=_literal_mapping(data["parameters"], f"{path}.parameters"),
        position=_pair(data["position"], f"{path}.position"),
        size=None if raw_size is None else _pair(raw_size, f"{path}.size"),
        user_label=label,
        collapsed=collapsed,
        ui_state=_literal_mapping(data["ui_state"], f"{path}.ui_state"),
    )


def _connection_from_data(value: object, index: int) -> ConnectionModel:
    path = f"$.connections[{index}]"
    data = _object(value, path)
    expected = {
        "id",
        "source_node_id",
        "source_port_id",
        "destination_node_id",
        "destination_port_id",
    }
    if set(data) != expected:
        raise ValueError(f"{path} fields do not match version 1")
    return ConnectionModel(
        id=_uuid(data["id"], f"{path}.id"),
        source_node_id=_uuid(data["source_node_id"], f"{path}.source_node_id"),
        source_port_id=_string(data["source_port_id"], f"{path}.source_port_id"),
        destination_node_id=_uuid(data["destination_node_id"], f"{path}.destination_node_id"),
        destination_port_id=_string(data["destination_port_id"], f"{path}.destination_port_id"),
    )


def _literal_to_data(value: LiteralValue) -> object:
    if isinstance(value, ColorValue):
        return {"$type": "COLOR", "r": value.r, "g": value.g, "b": value.b, "a": value.a}
    if isinstance(value, NumericMatrix):
        return [list(row) for row in value.rows]
    return value


def _literal(value: object, path: str) -> LiteralValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return _numeric_matrix(cast(object, value), path)
    if isinstance(value, dict):
        data = _object(cast(object, value), path)
        if set(data) != {"$type", "r", "g", "b", "a"} or data.get("$type") != "COLOR":
            raise ValueError(f"{path} contains an unknown structured literal")
        return ColorValue(
            _number(data["r"], f"{path}.r"),
            _number(data["g"], f"{path}.g"),
            _number(data["b"], f"{path}.b"),
            _number(data["a"], f"{path}.a"),
        )
    raise ValueError(f"{path} must contain a finite literal")


def _numeric_matrix(value: object, path: str) -> NumericMatrix:
    rows = _list(value, path)
    parsed_rows: list[tuple[float, ...]] = []
    for row_index, row in enumerate(rows):
        raw_row = _list(row, f"{path}[{row_index}]")
        parsed_rows.append(
            tuple(
                _number(item, f"{path}[{row_index}][{column_index}]")
                for column_index, item in enumerate(raw_row)
            )
        )
    try:
        return NumericMatrix(tuple(parsed_rows))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{path} contains an invalid numeric matrix: {error}") from error


def _literal_mapping(value: object, path: str) -> Mapping[str, LiteralValue]:
    data = _object(value, path)
    return {key: _literal(item, f"{path}.{key}") for key, item in data.items()}


def _object(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{path} must have string keys")
    return {cast(str, key): item for key, item in raw.items()}


def _list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return cast(list[object], value)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _uuid(value: object, path: str) -> UUID:
    try:
        return UUID(_string(value, path))
    except ValueError as error:
        raise ValueError(f"{path} must be a UUID") from error


def _pair(value: object, path: str) -> tuple[float, float]:
    items = _list(value, path)
    if len(items) != 2:
        raise ValueError(f"{path} must have two numbers")
    return _number(items[0], f"{path}[0]"), _number(items[1], f"{path}[1]")


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{path} must be finite") from error
    if not math.isfinite(result):
        raise ValueError(f"{path} must be finite")
    return result
