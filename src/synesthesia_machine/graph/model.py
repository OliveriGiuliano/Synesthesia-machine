"""Qt-independent mutable graph document and immutable runtime snapshot."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4

from synesthesia_machine.contracts import ColorValue, NumericMatrix

type LiteralValue = str | int | float | bool | ColorValue | NumericMatrix | None


def _empty_literals() -> dict[str, LiteralValue]:
    return {}


def _freeze_literals(values: Mapping[str, LiteralValue]) -> Mapping[str, LiteralValue]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class NodeModel:
    id: UUID
    type_id: str
    implementation_version: int
    parameters: Mapping[str, LiteralValue] = field(default_factory=_empty_literals)
    position: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] | None = None
    user_label: str | None = None
    collapsed: bool = False
    ui_state: Mapping[str, LiteralValue] = field(default_factory=_empty_literals)

    def __post_init__(self) -> None:
        if self.implementation_version < 1:
            msg = "implementation_version must be at least 1"
            raise ValueError(msg)
        object.__setattr__(self, "parameters", _freeze_literals(self.parameters))
        object.__setattr__(self, "ui_state", _freeze_literals(self.ui_state))


@dataclass(frozen=True, slots=True)
class ConnectionModel:
    id: UUID
    source_node_id: UUID
    source_port_id: str
    destination_node_id: UUID
    destination_port_id: str
    # Presentation-only editor state (e.g. link-pill visibility). Deliberately kept
    # out of structural identity: __hash__ excludes it (see below), so two
    # connections that differ only in ui_state hash-equal but compare unequal.
    ui_state: Mapping[str, LiteralValue] = field(default_factory=_empty_literals)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ui_state", _freeze_literals(self.ui_state))

    def __hash__(self) -> int:
        # ConnectionModel is used as a dict key in the compiler's baseline
        # cache, so it must be hashable. ui_state is presentation-only editor
        # state (not part of the connection's structural identity) and is a
        # frozen Mapping, which is itself unhashable, so exclude it.
        return hash(
            (
                self.id,
                self.source_node_id,
                self.source_port_id,
                self.destination_node_id,
                self.destination_port_id,
            )
        )


class GroupKind(StrEnum):
    GROUP = "GROUP"
    COMMENT = "COMMENT"


@dataclass(frozen=True, slots=True)
class GroupModel:
    """Persisted organizational rectangle or free-form canvas comment."""

    id: UUID
    kind: GroupKind
    title: str
    text: str
    position: tuple[float, float]
    size: tuple[float, float]
    color: str = "#46515f"

    def __post_init__(self) -> None:
        values = (*self.position, *self.size)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Group position and size must be finite")
        if self.size[0] <= 0.0 or self.size[1] <= 0.0:
            raise ValueError("Group size must be positive")
        if (
            len(self.color) != 7
            or not self.color.startswith("#")
            or any(character not in "0123456789abcdefABCDEF" for character in self.color[1:])
        ):
            raise ValueError("Group color must use #RRGGBB format")


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    document_id: UUID
    revision: int
    nodes: tuple[NodeModel, ...]
    connections: tuple[ConnectionModel, ...]
    document_settings: Mapping[str, LiteralValue] = field(default_factory=_empty_literals)
    groups: tuple[GroupModel, ...] = ()

    def __post_init__(self) -> None:
        if self.revision < 0:
            msg = "graph revision cannot be negative"
            raise ValueError(msg)
        object.__setattr__(self, "document_settings", _freeze_literals(self.document_settings))

    def node(self, node_id: UUID) -> NodeModel | None:
        return next((node for node in self.nodes if node.id == node_id), None)


class GraphDocument:
    """Mutable authoring model whose snapshots are immutable and deterministic."""

    def __init__(self, *, document_id: UUID | None = None) -> None:
        self.document_id = document_id or uuid4()
        self.revision = 0
        self._nodes: dict[UUID, NodeModel] = {}
        self._connections: dict[UUID, ConnectionModel] = {}
        self._groups: dict[UUID, GroupModel] = {}
        self._document_settings: dict[str, LiteralValue] = {}

    @classmethod
    def from_snapshot(cls, snapshot: GraphSnapshot) -> GraphDocument:
        document = cls(document_id=snapshot.document_id)
        document.revision = snapshot.revision
        document._nodes = {node.id: node for node in snapshot.nodes}
        document._connections = {connection.id: connection for connection in snapshot.connections}
        document._groups = {group.id: group for group in snapshot.groups}
        document._document_settings = dict(snapshot.document_settings)
        return document

    @property
    def nodes(self) -> tuple[NodeModel, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes, key=str))

    @property
    def connections(self) -> tuple[ConnectionModel, ...]:
        return tuple(self._connections[key] for key in sorted(self._connections, key=str))

    @property
    def groups(self) -> tuple[GroupModel, ...]:
        return tuple(self._groups[key] for key in sorted(self._groups, key=str))

    def node(self, node_id: UUID) -> NodeModel | None:
        """Return one immutable node value without exposing mutable storage."""

        return self._nodes.get(node_id)

    def connection(self, connection_id: UUID) -> ConnectionModel | None:
        """Return one immutable connection value without exposing mutable storage."""

        return self._connections.get(connection_id)

    def group(self, group_id: UUID) -> GroupModel | None:
        return self._groups.get(group_id)

    def add_group(
        self,
        kind: GroupKind,
        *,
        title: str,
        text: str = "",
        position: tuple[float, float] = (0.0, 0.0),
        size: tuple[float, float] = (480.0, 320.0),
        color: str = "#46515f",
        group_id: UUID | None = None,
    ) -> UUID:
        identifier = group_id or uuid4()
        self.restore_group(GroupModel(identifier, kind, title, text, position, size, color))
        return identifier

    def restore_group(self, group: GroupModel, *, replace_existing: bool = False) -> None:
        existing = self._groups.get(group.id)
        if existing is not None and not replace_existing:
            raise ValueError(f"Group already exists: {group.id}")
        if existing == group:
            return
        self._groups[group.id] = group
        self._touch()

    def remove_group(self, group_id: UUID) -> None:
        if group_id not in self._groups:
            raise KeyError(f"Unknown group: {group_id}")
        del self._groups[group_id]
        self._touch()

    def incoming_connection(self, node_id: UUID, port_id: str) -> ConnectionModel | None:
        """Return the single connection occupying an input, if any."""

        return next(
            (
                connection
                for connection in self.connections
                if connection.destination_node_id == node_id
                and connection.destination_port_id == port_id
            ),
            None,
        )

    def incident_connections(
        self, node_ids: set[UUID] | frozenset[UUID]
    ) -> tuple[ConnectionModel, ...]:
        """Return connections touching any of the supplied nodes."""

        return tuple(
            connection
            for connection in self.connections
            if connection.source_node_id in node_ids or connection.destination_node_id in node_ids
        )

    def add_node(
        self,
        type_id: str,
        *,
        implementation_version: int = 1,
        parameters: Mapping[str, LiteralValue] | None = None,
        node_id: UUID | None = None,
        position: tuple[float, float] = (0.0, 0.0),
    ) -> UUID:
        identifier = node_id or uuid4()
        if identifier in self._nodes:
            msg = f"Node already exists: {identifier}"
            raise ValueError(msg)
        self._nodes[identifier] = NodeModel(
            id=identifier,
            type_id=type_id,
            implementation_version=implementation_version,
            parameters=parameters or {},
            position=position,
        )
        self._touch()
        return identifier

    def restore_node(self, node: NodeModel, *, replace_existing: bool = False) -> None:
        """Restore a complete immutable node value for persistence/undo adapters."""

        existing = self._nodes.get(node.id)
        if existing is not None and not replace_existing:
            msg = f"Node already exists: {node.id}"
            raise ValueError(msg)
        if existing == node:
            return
        self._nodes[node.id] = node
        self._touch()

    def remove_node(self, node_id: UUID) -> None:
        if node_id not in self._nodes:
            msg = f"Unknown node: {node_id}"
            raise KeyError(msg)
        del self._nodes[node_id]
        self._connections = {
            identifier: connection
            for identifier, connection in self._connections.items()
            if connection.source_node_id != node_id and connection.destination_node_id != node_id
        }
        self._touch()

    def set_parameter(self, node_id: UUID, parameter_id: str, value: LiteralValue) -> None:
        node = self._require_node(node_id)
        parameters = dict(node.parameters)
        if parameters.get(parameter_id) == value and parameter_id in parameters:
            return
        parameters[parameter_id] = value
        self._nodes[node_id] = replace(node, parameters=parameters)
        self._touch()

    def set_position(self, node_id: UUID, position: tuple[float, float]) -> None:
        node = self._require_node(node_id)
        if node.position == position:
            return
        self._nodes[node_id] = replace(node, position=position)
        self._touch()

    def add_connection(
        self,
        source_node_id: UUID,
        source_port_id: str,
        destination_node_id: UUID,
        destination_port_id: str,
        *,
        connection_id: UUID | None = None,
        replace_existing: bool = True,
    ) -> UUID:
        self._require_node(source_node_id)
        self._require_node(destination_node_id)
        identifier = connection_id or uuid4()
        collision = self._connections.get(identifier)
        if collision is not None and not (
            replace_existing
            and collision.destination_node_id == destination_node_id
            and collision.destination_port_id == destination_port_id
        ):
            msg = f"Connection already exists: {identifier}"
            raise ValueError(msg)
        candidate = ConnectionModel(
            id=identifier,
            source_node_id=source_node_id,
            source_port_id=source_port_id,
            destination_node_id=destination_node_id,
            destination_port_id=destination_port_id,
        )
        connections = self._connections
        if replace_existing:
            connections = {
                identifier: connection
                for identifier, connection in connections.items()
                if not (
                    connection.destination_node_id == destination_node_id
                    and connection.destination_port_id == destination_port_id
                )
            }
        else:
            connections = dict(connections)
        connections[identifier] = candidate
        self._connections = connections
        self._touch()
        return identifier

    def restore_connection(
        self,
        connection: ConnectionModel,
        *,
        replace_existing_input: bool = False,
    ) -> None:
        """Restore a complete immutable connection value for persistence/undo adapters."""

        self._require_node(connection.source_node_id)
        self._require_node(connection.destination_node_id)
        existing = self._connections.get(connection.id)
        if existing is not None and existing != connection:
            msg = f"Connection already exists: {connection.id}"
            raise ValueError(msg)
        if existing == connection:
            return
        if replace_existing_input:
            self._connections = {
                identifier: current
                for identifier, current in self._connections.items()
                if not (
                    current.destination_node_id == connection.destination_node_id
                    and current.destination_port_id == connection.destination_port_id
                )
            }
        self._connections[connection.id] = connection
        self._touch()

    def remove_connection(self, connection_id: UUID) -> None:
        if connection_id not in self._connections:
            msg = f"Unknown connection: {connection_id}"
            raise KeyError(msg)
        del self._connections[connection_id]
        self._touch()

    def set_connection_ui_state(self, connection_id: UUID, key: str, value: LiteralValue) -> None:
        """Change one presentation-only key without touching structural identity."""

        connection = self._require_connection(connection_id)
        ui_state = dict(connection.ui_state)
        if ui_state.get(key) == value and key in ui_state:
            return
        ui_state[key] = value
        self._connections[connection_id] = replace(connection, ui_state=ui_state)
        self._touch()

    def remove_connection_ui_state_key(self, connection_id: UUID, key: str) -> None:
        """Remove one presentation-only key, restoring the presentation default."""

        connection = self._require_connection(connection_id)
        if key not in connection.ui_state:
            return
        ui_state = dict(connection.ui_state)
        del ui_state[key]
        self._connections[connection_id] = replace(connection, ui_state=ui_state)
        self._touch()

    def set_document_setting(self, key: str, value: LiteralValue) -> None:
        if self._document_settings.get(key) == value and key in self._document_settings:
            return
        self._document_settings[key] = value
        self._touch()

    def snapshot(self) -> GraphSnapshot:
        return GraphSnapshot(
            document_id=self.document_id,
            revision=self.revision,
            nodes=self.nodes,
            connections=self.connections,
            document_settings=self._document_settings,
            groups=self.groups,
        )

    def _require_node(self, node_id: UUID) -> NodeModel:
        node = self._nodes.get(node_id)
        if node is None:
            msg = f"Unknown node: {node_id}"
            raise KeyError(msg)
        return node

    def _require_connection(self, connection_id: UUID) -> ConnectionModel:
        connection = self._connections.get(connection_id)
        if connection is None:
            msg = f"Unknown connection: {connection_id}"
            raise KeyError(msg)
        return connection

    def _touch(self) -> None:
        self.revision += 1
