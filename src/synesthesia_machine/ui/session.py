"""Document/session/controller ownership for the Phase 2 editor."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4
from weakref import ref

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QUndoCommand, QUndoStack

from synesthesia_machine.graph import (
    ConnectionModel,
    GraphCompiler,
    GraphDocument,
    LiteralValue,
    NodeModel,
    ValidationReport,
)
from synesthesia_machine.nodes import NodeDefinition, NodeRegistry
from synesthesia_machine.persistence import (
    ClipboardFragment,
    copy_fragment,
    load_graph,
    remap_fragment,
    save_graph,
)
from synesthesia_machine.ui.commands import (
    AddConnectionCommand,
    AddNodeCommand,
    DeleteNodesCommand,
    DuplicateCommand,
    MoveNodesCommand,
    PasteCommand,
    RemoveConnectionCommand,
    ReplaceConnectionCommand,
    SetParameterCommand,
)
from synesthesia_machine.ui.view_models import GraphViewModel, project_graph


class DocumentSession(QObject):
    """Own the single mutable document, undo stack, path, and validation projection."""

    changed = Signal()
    pathChanged = Signal(object)
    dirtyChanged = Signal(bool)
    validationChanged = Signal(object)

    def __init__(self, registry: NodeRegistry, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.compiler = GraphCompiler(registry)
        self.document = GraphDocument()
        self.undo_stack = QUndoStack(self)
        session_reference = ref(self)

        def refresh_session() -> None:
            if (session := session_reference()) is not None:
                session._refresh()

        self._command_change_callback = refresh_session
        self.current_path: Path | None = None
        self.report = ValidationReport()
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)
        self.undo_stack.setClean()
        self._refresh()

    @property
    def is_dirty(self) -> bool:
        return not self.undo_stack.isClean()

    @property
    def view_model(self) -> GraphViewModel:
        return project_graph(self.document.snapshot(), self.registry, self.report)

    def push(self, command: QUndoCommand) -> None:
        self.undo_stack.push(command)

    def new_document(self) -> None:
        self.document = GraphDocument()
        self.current_path = None
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self.pathChanged.emit(None)
        self._refresh()

    def open_document(self, path: str | Path) -> None:
        source = Path(path).resolve()
        self.document = GraphDocument.from_snapshot(load_graph(source, self.registry))
        self.current_path = source
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self.pathChanged.emit(source)
        self._refresh()

    def recover_document(
        self,
        recovery_path: str | Path,
        *,
        explicit_path: Path | None = None,
    ) -> None:
        """Load a recovery snapshot while retaining dirty-document semantics."""

        self.document = GraphDocument.from_snapshot(load_graph(recovery_path, self.registry))
        self.current_path = explicit_path.resolve() if explicit_path is not None else None
        self.undo_stack.clear()
        self.undo_stack.resetClean()
        self.pathChanged.emit(self.current_path)
        self._refresh()

    def save(self, path: str | Path | None = None) -> Path:
        destination = Path(path).resolve() if path is not None else self.current_path
        if destination is None:
            raise ValueError("A destination path is required")
        save_graph(destination, self.document.snapshot())
        self.current_path = destination
        self.undo_stack.setClean()
        self.pathChanged.emit(destination)
        return destination

    def add_node(self, type_id: str, position: tuple[float, float]) -> UUID:
        definition = self.registry.require(type_id)
        node = NodeModel(uuid4(), type_id, definition.implementation_version, position=position)
        self.push(AddNodeCommand(self.document, node, self._command_change_callback))
        return node.id

    def delete_nodes(self, node_ids: set[UUID]) -> None:
        if node_ids:
            self.push(DeleteNodesCommand(self.document, node_ids, self._command_change_callback))

    def delete_selection(self, node_ids: set[UUID], connection_ids: set[UUID]) -> None:
        if not node_ids and not connection_ids:
            return
        incident_ids = {
            connection.id for connection in self.document.incident_connections(node_ids)
        }
        independent_connections = connection_ids - incident_ids
        self.undo_stack.beginMacro("Delete selection")
        try:
            for connection_id in sorted(independent_connections, key=str):
                self.remove_connection(connection_id)
            self.delete_nodes(node_ids)
        finally:
            self.undo_stack.endMacro()

    def move_nodes(
        self,
        old_positions: dict[UUID, tuple[float, float]],
        new_positions: dict[UUID, tuple[float, float]],
    ) -> None:
        if old_positions != new_positions:
            self.push(
                MoveNodesCommand(
                    self.document,
                    old_positions,
                    new_positions,
                    self._command_change_callback,
                )
            )

    def set_parameter(self, node_id: UUID, parameter_id: str, value: LiteralValue) -> None:
        node = self.document.node(node_id)
        if node is not None and node.parameters.get(parameter_id) == value:
            return
        self.push(
            SetParameterCommand(
                self.document,
                node_id,
                parameter_id,
                value,
                self._command_change_callback,
            )
        )

    def add_connection(
        self,
        source_node_id: UUID,
        source_port_id: str,
        destination_node_id: UUID,
        destination_port_id: str,
    ) -> UUID:
        connection = ConnectionModel(
            uuid4(), source_node_id, source_port_id, destination_node_id, destination_port_id
        )
        occupied = self.document.incoming_connection(destination_node_id, destination_port_id)
        command: QUndoCommand
        if occupied is None:
            command = AddConnectionCommand(
                self.document,
                self.compiler,
                connection,
                self._command_change_callback,
            )
        else:
            command = ReplaceConnectionCommand(
                self.document,
                self.compiler,
                connection,
                self._command_change_callback,
            )
        self.push(command)
        return connection.id

    def remove_connection(self, connection_id: UUID) -> None:
        self.push(
            RemoveConnectionCommand(
                self.document,
                connection_id,
                self._command_change_callback,
            )
        )

    def compatibility(
        self,
        source_node_id: UUID,
        source_port_id: str,
        destination_node_id: UUID,
        destination_port_id: str,
    ) -> bool:
        return self.compiler.connection_compatibility(
            self.document.snapshot(),
            source_node_id,
            source_port_id,
            destination_node_id,
            destination_port_id,
        ).accepted

    def copy(self, node_ids: set[UUID]) -> ClipboardFragment:
        return copy_fragment(self.document.snapshot(), node_ids)

    def paste(self, fragment: ClipboardFragment) -> set[UUID]:
        remapped = remap_fragment(fragment)
        self.push(PasteCommand(self.document, remapped, self._command_change_callback))
        return {node.id for node in remapped.nodes}

    def duplicate(self, node_ids: set[UUID]) -> set[UUID]:
        remapped = remap_fragment(self.copy(node_ids))
        self.push(DuplicateCommand(self.document, remapped, self._command_change_callback))
        return {node.id for node in remapped.nodes}

    def compatible_definitions(
        self,
        node_id: UUID,
        port_id: str,
        is_output: bool,
    ) -> tuple[tuple[NodeDefinition, str], ...]:
        results: list[tuple[NodeDefinition, str]] = []
        for definition in self.registry.definitions():
            temporary = GraphDocument.from_snapshot(self.document.snapshot())
            candidate_id = temporary.add_node(
                definition.type_id, implementation_version=definition.implementation_version
            )
            if is_output:
                candidates = (
                    *(port.id for port in definition.inputs),
                    *(parameter.id for parameter in definition.parameters if parameter.connectable),
                )
                pairs = ((node_id, port_id, candidate_id, candidate) for candidate in candidates)
            else:
                pairs = (
                    (candidate_id, output.id, node_id, port_id) for output in definition.outputs
                )
            for source_node, source_port, destination_node, destination_port in pairs:
                if self.compiler.connection_compatibility(
                    temporary.snapshot(),
                    source_node,
                    source_port,
                    destination_node,
                    destination_port,
                ).accepted:
                    results.append((definition, destination_port if is_output else source_port))
                    break
        return tuple(results)

    def insert_and_connect(
        self,
        definition: NodeDefinition,
        new_port_id: str,
        existing_node_id: UUID,
        existing_port_id: str,
        existing_is_output: bool,
        position: tuple[float, float],
    ) -> UUID:
        self.undo_stack.beginMacro("Insert and connect node")
        try:
            node_id = self.add_node(definition.type_id, position)
            if existing_is_output:
                self.add_connection(existing_node_id, existing_port_id, node_id, new_port_id)
            else:
                self.add_connection(node_id, new_port_id, existing_node_id, existing_port_id)
        finally:
            self.undo_stack.endMacro()
        return node_id

    def _refresh(self) -> None:
        self.report = self.compiler.compile(self.document.snapshot()).report
        self.validationChanged.emit(self.report)
        self.changed.emit()

    @Slot(bool)
    def _on_clean_changed(self, clean: bool) -> None:
        self.dirtyChanged.emit(not clean)
