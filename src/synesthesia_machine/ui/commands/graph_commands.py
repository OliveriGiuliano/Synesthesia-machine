"""QUndoCommand adapters that are the only UI mutation path into GraphDocument."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from uuid import UUID, uuid4

from PySide6.QtGui import QUndoCommand

from synesthesia_machine.graph import (
    ConnectionModel,
    GraphCompiler,
    GraphDocument,
    GroupModel,
    LiteralValue,
    NodeModel,
)
from synesthesia_machine.persistence.clipboard import ClipboardFragment
from synesthesia_machine.persistence.media_relink import relinked_media_node
from synesthesia_machine.ui.translations import tr

type ChangeCallback = Callable[[], None]


class IncompatibleConnectionError(ValueError):
    """Raised before a command is pushed when compiler semantics reject its edge."""


class _DocumentCommand(QUndoCommand):
    def __init__(
        self,
        text: str,
        document: GraphDocument,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__(tr(text))
        self.document = document
        self._on_changed = on_changed

    def _changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()


class AddNodeCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        node: NodeModel,
        on_changed: ChangeCallback | None = None,
        *,
        replaced_node_ids: set[UUID] | None = None,
    ) -> None:
        super().__init__("Add node", document, on_changed)
        self.node = node
        selected = frozenset(replaced_node_ids or ())
        self.replaced_nodes = tuple(item for item in document.nodes if item.id in selected)
        if len(self.replaced_nodes) != len(selected):
            raise KeyError("Visualizer replacement contains an unknown node")
        self.replaced_connections = document.incident_connections(selected)

    def redo(self) -> None:
        for replaced in self.replaced_nodes:
            if self.document.node(replaced.id) is not None:
                self.document.remove_node(replaced.id)
        self.document.restore_node(self.node)
        self._changed()

    def undo(self) -> None:
        self.document.remove_node(self.node.id)
        for replaced in self.replaced_nodes:
            self.document.restore_node(replaced)
        for connection in self.replaced_connections:
            self.document.restore_connection(connection)
        self._changed()


class AddGroupCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        group: GroupModel,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Add canvas group", document, on_changed)
        self.group = group

    def redo(self) -> None:
        self.document.restore_group(self.group)
        self._changed()

    def undo(self) -> None:
        self.document.remove_group(self.group.id)
        self._changed()


class DeleteGroupsCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        group_ids: set[UUID],
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Delete canvas groups", document, on_changed)
        selected = frozenset(group_ids)
        self.groups = tuple(group for group in document.groups if group.id in selected)

    def redo(self) -> None:
        for group in self.groups:
            if self.document.group(group.id) is not None:
                self.document.remove_group(group.id)
        self._changed()

    def undo(self) -> None:
        for group in self.groups:
            self.document.restore_group(group)
        self._changed()


class MoveGroupsCommand(_DocumentCommand):
    _COMMAND_ID = 0x534D03

    def __init__(
        self,
        document: GraphDocument,
        old_positions: Mapping[UUID, tuple[float, float]],
        new_positions: Mapping[UUID, tuple[float, float]],
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Move canvas groups", document, on_changed)
        if set(old_positions) != set(new_positions):
            raise ValueError("Group move position maps must contain the same IDs")
        self.old_positions = dict(old_positions)
        self.new_positions = dict(new_positions)

    def id(self) -> int:
        return self._COMMAND_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, MoveGroupsCommand):
            return False
        if other.document is not self.document or set(other.new_positions) != set(
            self.new_positions
        ):
            return False
        self.new_positions = dict(other.new_positions)
        return True

    def redo(self) -> None:
        self._apply(self.new_positions)

    def undo(self) -> None:
        self._apply(self.old_positions)

    def _apply(self, positions: Mapping[UUID, tuple[float, float]]) -> None:
        for group_id in sorted(positions, key=str):
            group = self.document.group(group_id)
            if group is None:
                raise KeyError(f"Unknown group: {group_id}")
            self.document.restore_group(
                replace(group, position=positions[group_id]), replace_existing=True
            )
        self._changed()


class EditGroupCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        new_group: GroupModel,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Edit canvas group", document, on_changed)
        old_group = document.group(new_group.id)
        if old_group is None:
            raise KeyError(f"Unknown group: {new_group.id}")
        self.old_group = old_group
        self.new_group = new_group

    def redo(self) -> None:
        self.document.restore_group(self.new_group, replace_existing=True)
        self._changed()

    def undo(self) -> None:
        self.document.restore_group(self.old_group, replace_existing=True)
        self._changed()


class DeleteNodesCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        node_ids: set[UUID],
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Delete nodes", document, on_changed)
        selected = frozenset(node_ids)
        self.nodes = tuple(node for node in document.nodes if node.id in selected)
        present = {node.id for node in self.nodes}
        self.connections = document.incident_connections(present)

    def redo(self) -> None:
        for node in self.nodes:
            if self.document.node(node.id) is not None:
                self.document.remove_node(node.id)
        self._changed()

    def undo(self) -> None:
        for node in self.nodes:
            self.document.restore_node(node)
        for connection in self.connections:
            self.document.restore_connection(connection)
        self._changed()


class MoveNodesCommand(_DocumentCommand):
    _COMMAND_ID = 0x534D01

    def __init__(
        self,
        document: GraphDocument,
        old_positions: Mapping[UUID, tuple[float, float]],
        new_positions: Mapping[UUID, tuple[float, float]],
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Move nodes", document, on_changed)
        if set(old_positions) != set(new_positions):
            msg = "Move command position maps must contain the same node IDs"
            raise ValueError(msg)
        self.old_positions = dict(old_positions)
        self.new_positions = dict(new_positions)

    def id(self) -> int:
        return self._COMMAND_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, MoveNodesCommand):
            return False
        if other.document is not self.document or set(other.new_positions) != set(
            self.new_positions
        ):
            return False
        self.new_positions = dict(other.new_positions)
        return True

    def redo(self) -> None:
        self._apply(self.new_positions)

    def undo(self) -> None:
        self._apply(self.old_positions)

    def _apply(self, positions: Mapping[UUID, tuple[float, float]]) -> None:
        for node_id in sorted(positions, key=str):
            self.document.set_position(node_id, positions[node_id])
        self._changed()


class SetParameterCommand(_DocumentCommand):
    _COMMAND_ID = 0x534D02

    def __init__(
        self,
        document: GraphDocument,
        node_id: UUID,
        parameter_id: str,
        value: LiteralValue,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Set parameter", document, on_changed)
        node = document.node(node_id)
        if node is None:
            raise KeyError(f"Unknown node: {node_id}")
        values = dict(node.parameters)
        values[parameter_id] = value
        self.node_id = node_id
        self.parameter_id = parameter_id
        self.old_node = node
        self.new_node = replace(node, parameters=values)

    def id(self) -> int:
        return self._COMMAND_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, SetParameterCommand):
            return False
        if (
            other.document is not self.document
            or other.node_id != self.node_id
            or other.parameter_id != self.parameter_id
        ):
            return False
        self.new_node = other.new_node
        return True

    def redo(self) -> None:
        self.document.restore_node(self.new_node, replace_existing=True)
        self._changed()

    def undo(self) -> None:
        self.document.restore_node(self.old_node, replace_existing=True)
        self._changed()


PREVIEW_VISIBLE_KEY = "preview_visible"


class SetConnectionPreviewCommand(_DocumentCommand):
    """Show or hide the live preview pill on a single connection."""

    _COMMAND_ID = 0x534D04

    def __init__(
        self,
        document: GraphDocument,
        connection_id: UUID,
        visible: bool,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Set connection preview visibility", document, on_changed)
        connection = document.connection(connection_id)
        if connection is None:
            raise KeyError(f"Unknown connection: {connection_id}")
        self.connection_id = connection_id
        # Capture the exact prior representation (``None`` when the key is absent,
        # which is the "visible" default) so undo restores the state precisely.
        self._old_raw = connection.ui_state.get(PREVIEW_VISIBLE_KEY)
        self._new_raw = visible

    def id(self) -> int:
        return self._COMMAND_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        return False

    def redo(self) -> None:
        self._apply(self._new_raw)

    def undo(self) -> None:
        self._apply(self._old_raw)

    def _apply(self, raw: LiteralValue | None) -> None:
        if raw is None:
            self.document.remove_connection_ui_state_key(self.connection_id, PREVIEW_VISIBLE_KEY)
        else:
            self.document.set_connection_ui_state(self.connection_id, PREVIEW_VISIBLE_KEY, raw)
        self._changed()


class RandomizeParametersCommand(_DocumentCommand):
    """Replace parameter mappings for several nodes as one undoable document edit."""

    def __init__(
        self,
        document: GraphDocument,
        nodes: Mapping[UUID, NodeModel],
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Randomize parameters", document, on_changed)
        self.new_nodes = tuple(nodes[node_id] for node_id in sorted(nodes, key=str))
        old_nodes: list[NodeModel] = []
        for node in self.new_nodes:
            old = document.node(node.id)
            if old is None:
                raise KeyError(f"Unknown node: {node.id}")
            old_nodes.append(old)
        self.old_nodes = tuple(old_nodes)

    def redo(self) -> None:
        self._apply(self.new_nodes)

    def undo(self) -> None:
        self._apply(self.old_nodes)

    def _apply(self, nodes: tuple[NodeModel, ...]) -> None:
        for node in nodes:
            self.document.restore_node(node, replace_existing=True)
        self._changed()


class RandomizeNodesCommand(_DocumentCommand):
    """Swap a selected subgraph for a compiler-valid randomized replacement."""

    def __init__(
        self,
        document: GraphDocument,
        old_node_ids: set[UUID],
        replacement_nodes: Mapping[UUID, NodeModel],
        replacement_connections: tuple[ConnectionModel, ...],
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Randomize nodes", document, on_changed)
        selected = frozenset(old_node_ids)
        self.old_nodes = tuple(node for node in document.nodes if node.id in selected)
        if len(self.old_nodes) != len(selected):
            raise KeyError("Randomize Nodes selection contains an unknown node")
        self.old_connections = document.incident_connections(selected)
        self.new_nodes = tuple(
            replacement_nodes[node_id] for node_id in sorted(replacement_nodes, key=str)
        )
        self.new_connections = replacement_connections

    def redo(self) -> None:
        self._swap(
            self.old_nodes,
            self.old_connections,
            self.new_nodes,
            self.new_connections,
        )

    def undo(self) -> None:
        self._swap(
            self.new_nodes,
            self.new_connections,
            self.old_nodes,
            self.old_connections,
        )

    def _swap(
        self,
        removed_nodes: tuple[NodeModel, ...],
        removed_connections: tuple[ConnectionModel, ...],
        added_nodes: tuple[NodeModel, ...],
        added_connections: tuple[ConnectionModel, ...],
    ) -> None:
        for connection in removed_connections:
            if self.document.connection(connection.id) is not None:
                self.document.remove_connection(connection.id)
        for node in removed_nodes:
            if self.document.node(node.id) is not None:
                self.document.remove_node(node.id)
        for node in added_nodes:
            self.document.restore_node(node)
        for connection in added_connections:
            self.document.restore_connection(connection)
        self._changed()


class RelinkMediaCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        node_id: UUID,
        candidate: str,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Relink missing media", document, on_changed)
        node = document.node(node_id)
        if node is None:
            raise KeyError(f"Unknown node: {node_id}")
        self.old_node = node
        self.new_node = relinked_media_node(node, candidate)

    def redo(self) -> None:
        self.document.restore_node(self.new_node, replace_existing=True)
        self._changed()

    def undo(self) -> None:
        self.document.restore_node(self.old_node, replace_existing=True)
        self._changed()


class AddConnectionCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        compiler: GraphCompiler,
        connection: ConnectionModel,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        occupied = document.incoming_connection(
            connection.destination_node_id, connection.destination_port_id
        )
        if occupied is not None:
            msg = "Destination input is occupied; use ReplaceConnectionCommand"
            raise ValueError(msg)
        compatibility = compiler.connection_compatibility(
            document.snapshot(),
            connection.source_node_id,
            connection.source_port_id,
            connection.destination_node_id,
            connection.destination_port_id,
        )
        if not compatibility.accepted:
            detail = "; ".join(issue.message for issue in compatibility.issues)
            raise IncompatibleConnectionError(detail or "Connection is incompatible")
        super().__init__("Add connection", document, on_changed)
        self.connection = connection

    def redo(self) -> None:
        self.document.restore_connection(self.connection)
        self._changed()

    def undo(self) -> None:
        self.document.remove_connection(self.connection.id)
        self._changed()


class ReplaceConnectionCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        compiler: GraphCompiler,
        connection: ConnectionModel,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        compatibility = compiler.connection_compatibility(
            document.snapshot(),
            connection.source_node_id,
            connection.source_port_id,
            connection.destination_node_id,
            connection.destination_port_id,
        )
        if not compatibility.accepted:
            detail = "; ".join(issue.message for issue in compatibility.issues)
            raise IncompatibleConnectionError(detail or "Connection is incompatible")
        super().__init__("Replace connection", document, on_changed)
        self.connection = connection
        self.replaced = document.incoming_connection(
            connection.destination_node_id, connection.destination_port_id
        )

    def redo(self) -> None:
        self.document.restore_connection(self.connection, replace_existing_input=True)
        self._changed()

    def undo(self) -> None:
        self.document.remove_connection(self.connection.id)
        if self.replaced is not None:
            self.document.restore_connection(self.replaced)
        self._changed()


class RemoveConnectionCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        connection_id: UUID,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__("Remove connection", document, on_changed)
        connection = document.connection(connection_id)
        if connection is None:
            raise KeyError(f"Unknown connection: {connection_id}")
        self.connection = connection

    def redo(self) -> None:
        self.document.remove_connection(self.connection.id)
        self._changed()

    def undo(self) -> None:
        self.document.restore_connection(self.connection)
        self._changed()


class PasteCommand(_DocumentCommand):
    def __init__(
        self,
        document: GraphDocument,
        fragment: ClipboardFragment,
        on_changed: ChangeCallback | None = None,
        *,
        text: str = "Paste nodes",
    ) -> None:
        super().__init__(text, document, on_changed)
        self.fragment = fragment

    def redo(self) -> None:
        for node in self.fragment.nodes:
            self.document.restore_node(node)
        for connection in self.fragment.connections:
            self.document.restore_connection(connection)
        self._changed()

    def undo(self) -> None:
        for node in self.fragment.nodes:
            if self.document.node(node.id) is not None:
                self.document.remove_node(node.id)
        self._changed()


class DuplicateCommand(PasteCommand):
    def __init__(
        self,
        document: GraphDocument,
        fragment: ClipboardFragment,
        on_changed: ChangeCallback | None = None,
    ) -> None:
        super().__init__(document, fragment, on_changed, text="Duplicate nodes")


def new_connection(
    source_node_id: UUID,
    source_port_id: str,
    destination_node_id: UUID,
    destination_port_id: str,
) -> ConnectionModel:
    """Create a fresh connection value for controllers and tests."""

    return ConnectionModel(
        id=uuid4(),
        source_node_id=source_node_id,
        source_port_id=source_port_id,
        destination_node_id=destination_node_id,
        destination_port_id=destination_port_id,
    )
