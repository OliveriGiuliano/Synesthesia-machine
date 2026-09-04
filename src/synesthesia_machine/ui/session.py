"""Document, session, and controller ownership for the node editor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4
from weakref import ref

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QUndoCommand, QUndoStack

from synesthesia_machine.contracts import DeviceCatalogue, DeviceKind
from synesthesia_machine.graph import (
    CompilationResult,
    ConnectionModel,
    GraphCompiler,
    GraphDocument,
    GraphSnapshot,
    GroupKind,
    GroupModel,
    LiteralValue,
    NodeModel,
    ValidationReport,
    randomize_graph_nodes,
    randomize_graph_parameters,
)
from synesthesia_machine.nodes import NodeDefinition, NodeRegistry
from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
from synesthesia_machine.persistence import (
    ClipboardFragment,
    copy_fragment,
    load_graph,
    remap_fragment,
    save_graph,
)
from synesthesia_machine.ui.commands import (
    PREVIEW_VISIBLE_KEY,
    AddConnectionCommand,
    AddGroupCommand,
    AddNodeCommand,
    DeleteGroupsCommand,
    DeleteNodesCommand,
    DuplicateCommand,
    EditGroupCommand,
    MoveGroupsCommand,
    MoveNodesCommand,
    PasteCommand,
    RandomizeNodesCommand,
    RandomizeParametersCommand,
    RelinkMediaCommand,
    RemoveConnectionCommand,
    ReplaceConnectionCommand,
    SetConnectionPreviewCommand,
    SetParameterCommand,
)
from synesthesia_machine.ui.translations import tr, trf
from synesthesia_machine.ui.view_models import GraphViewModel, ParameterViewModel, project_graph

_IMAGE_VISUALIZER_TYPE_IDS = frozenset({DISPLAY_IMAGE_DATA_TYPE_ID, CHANNEL_DISPLAY_TYPE_ID})
_NOTE_VISUALIZER_TYPE_IDS = frozenset({NOTE_VISUALIZER_TYPE_ID})


class DocumentSession(QObject):
    """Own the single mutable document, undo stack, path, and validation projection."""

    changed = Signal()
    runtimeChanged = Signal()
    pathChanged = Signal(object)
    dirtyChanged = Signal(bool)
    validationChanged = Signal(object)
    deviceCatalogueChanged = Signal()

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

        def refresh_presentation() -> None:
            if (session := session_reference()) is not None:
                session._refresh(runtime_changed=False)

        self._command_change_callback = refresh_session
        self._presentation_change_callback = refresh_presentation
        self.current_path: Path | None = None
        self.report = ValidationReport()
        self._device_catalogue = DeviceCatalogue()
        self._compilation: CompilationResult
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)
        self.undo_stack.setClean()
        self._refresh()

    @property
    def is_dirty(self) -> bool:
        return not self.undo_stack.isClean()

    @property
    def view_model(self) -> GraphViewModel:
        return project_graph(
            self.document.snapshot(),
            self.registry,
            self.report,
            compilation=self._compilation,
        )

    def push(self, command: QUndoCommand) -> None:
        self.undo_stack.push(command)

    def set_device_catalogue(self, catalogue: DeviceCatalogue) -> None:
        if catalogue == self._device_catalogue:
            return
        devices_changed = catalogue.devices != self._device_catalogue.devices
        self._device_catalogue = catalogue
        if devices_changed:
            self.deviceCatalogueChanged.emit()

    def device_parameter_choices(
        self, parameter: ParameterViewModel
    ) -> tuple[tuple[str, LiteralValue], ...]:
        kind = parameter.spec.device_kind
        if kind is None:
            return ()
        choices = [
            (
                tr(device.display_name) if device.is_default else device.display_name,
                device.device_id,
            )
            for device in sorted(
                self._device_catalogue.for_kind(kind),
                key=lambda item: (
                    not item.is_default,
                    item.display_name.casefold(),
                    item.device_id,
                ),
            )
        ]
        if kind is DeviceKind.MIDI_OUTPUT and not any(value == "" for _label, value in choices):
            choices.insert(0, (tr("No MIDI output"), ""))
        if kind is DeviceKind.AUDIO_OUTPUT and not any(value == "" for _label, value in choices):
            choices.insert(0, (tr("System default audio output"), ""))
        current = parameter.value
        if isinstance(current, str) and all(value != current for _label, value in choices):
            choices.append((trf("{device} (unavailable)", device=current), current))
        return tuple(choices)

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

    def replace_with_snapshot(self, snapshot: GraphSnapshot) -> None:
        """Install a generated graph as a new unsaved, dirty document."""

        self.document = GraphDocument.from_snapshot(snapshot)
        self.current_path = None
        self.undo_stack.clear()
        self.undo_stack.resetClean()
        self.pathChanged.emit(None)
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

    def add_node(
        self,
        type_id: str,
        position: tuple[float, float],
        *,
        parameters: Mapping[str, LiteralValue] | None = None,
    ) -> UUID:
        """Add one node as a single undo step, optionally with initial parameters.

        Explicit parameters are sanitized against the node definition so the
        committed document stays canonical (mirrors set_parameter semantics).
        """

        definition = self.registry.require(type_id)
        initial_parameters: dict[str, LiteralValue] = {}
        if parameters is not None:
            for parameter_id, value in parameters.items():
                spec = definition.parameter(parameter_id)
                if spec is None:
                    raise KeyError(f"Unknown parameter {parameter_id!r} on {type_id}")
                initial_parameters[parameter_id] = spec.sanitize_value(value)
        node = NodeModel(
            uuid4(),
            type_id,
            definition.implementation_version,
            position=position,
            parameters=initial_parameters,
        )
        replaced_node_ids = {
            existing.id
            for existing in self.document.nodes
            if existing.type_id in _visualizer_type_ids_for(type_id)
        }
        self.push(
            AddNodeCommand(
                self.document,
                node,
                self._command_change_callback,
                replaced_node_ids=replaced_node_ids,
            )
        )
        return node.id

    def add_group(
        self,
        kind: GroupKind,
        position: tuple[float, float],
        *,
        title: str,
        text: str = "",
        size: tuple[float, float] = (480.0, 320.0),
        color: str = "#46515f",
    ) -> UUID:
        group = GroupModel(uuid4(), kind, title, text, position, size, color)
        self.push(AddGroupCommand(self.document, group, self._presentation_change_callback))
        return group.id

    def delete_groups(self, group_ids: set[UUID]) -> None:
        if group_ids:
            self.push(
                DeleteGroupsCommand(
                    self.document,
                    group_ids,
                    self._presentation_change_callback,
                )
            )

    def move_groups(
        self,
        old_positions: dict[UUID, tuple[float, float]],
        new_positions: dict[UUID, tuple[float, float]],
    ) -> None:
        if old_positions != new_positions:
            self.push(
                MoveGroupsCommand(
                    self.document,
                    old_positions,
                    new_positions,
                    self._presentation_change_callback,
                )
            )

    def update_group(
        self,
        group_id: UUID,
        *,
        title: str | None = None,
        text: str | None = None,
        position: tuple[float, float] | None = None,
        size: tuple[float, float] | None = None,
        color: str | None = None,
    ) -> None:
        group = self.document.group(group_id)
        if group is None:
            raise KeyError(f"Unknown group: {group_id}")
        updated = replace(
            group,
            title=group.title if title is None else title,
            text=group.text if text is None else text,
            position=group.position if position is None else position,
            size=group.size if size is None else size,
            color=group.color if color is None else color,
        )
        if updated != group:
            self.push(
                EditGroupCommand(
                    self.document,
                    updated,
                    self._presentation_change_callback,
                )
            )

    def delete_nodes(self, node_ids: set[UUID]) -> None:
        if node_ids:
            self.push(DeleteNodesCommand(self.document, node_ids, self._command_change_callback))

    def delete_selection(
        self,
        node_ids: set[UUID],
        connection_ids: set[UUID],
        group_ids: set[UUID] | None = None,
    ) -> None:
        selected_groups: set[UUID] = set() if group_ids is None else group_ids
        if not node_ids and not connection_ids and not selected_groups:
            return
        incident_ids = {
            connection.id for connection in self.document.incident_connections(node_ids)
        }
        independent_connections = connection_ids - incident_ids
        self.undo_stack.beginMacro(tr("Delete selection"))
        try:
            for connection_id in sorted(independent_connections, key=str):
                self.remove_connection(connection_id)
            self.delete_nodes(node_ids)
            self.delete_groups(selected_groups)
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
                    self._presentation_change_callback,
                )
            )

    def set_parameter(self, node_id: UUID, parameter_id: str, value: LiteralValue) -> None:
        node = self.document.node(node_id)
        if node is None:
            raise KeyError(f"Unknown node: {node_id}")
        parameter = self.registry.require(node.type_id).parameter(parameter_id)
        if parameter is None:
            raise KeyError(f"Unknown parameter {parameter_id!r} on {node.type_id}")
        sanitized = parameter.sanitize_value(value)
        if node.parameters.get(parameter_id) == sanitized:
            return
        self.push(
            SetParameterCommand(
                self.document,
                node_id,
                parameter_id,
                sanitized,
                self._command_change_callback,
            )
        )

    def set_connection_preview_visible(self, connection_id: UUID, visible: bool) -> None:
        connection = self.document.connection(connection_id)
        if connection is None:
            return
        if bool(connection.ui_state.get(PREVIEW_VISIBLE_KEY, True)) == visible:
            return
        self.push(
            SetConnectionPreviewCommand(
                self.document,
                connection_id,
                visible,
                self._command_change_callback,
            )
        )

    def randomize_parameters(
        self,
        node_ids: set[UUID],
        *,
        seed: int | None = None,
    ) -> tuple[int, int]:
        """Randomize selected node literals as one undoable, compiler-safe operation."""

        original = self.document.snapshot()
        randomized = randomize_graph_parameters(original, self.registry, node_ids, seed=seed)
        original_nodes = {node.id: node for node in original.nodes}
        changed_nodes = {
            node.id: node
            for node in randomized.nodes
            if node.id in node_ids and node.parameters != original_nodes[node.id].parameters
        }
        if not changed_nodes:
            return 0, 0
        parameter_count = 0
        for node_id, node in changed_nodes.items():
            definition = self.registry.require(node.type_id)
            original_values, _errors = definition.parameter_values(
                original_nodes[node_id].parameters
            )
            parameter_count += sum(
                value != original_values[parameter_id]
                for parameter_id, value in node.parameters.items()
            )
        self.push(
            RandomizeParametersCommand(
                self.document,
                changed_nodes,
                self._command_change_callback,
            )
        )
        return len(changed_nodes), parameter_count

    def randomize_nodes(
        self,
        node_ids: set[UUID],
        *,
        seed: int | None = None,
    ) -> frozenset[UUID]:
        """Replace selected nodes and their incident edges as one undoable edit."""

        original = self.document.snapshot()
        randomized, replacement_ids = randomize_graph_nodes(
            original,
            self.registry,
            node_ids,
            seed=seed,
        )
        if randomized == original:
            return frozenset()
        replacement_nodes = {
            node.id: node for node in randomized.nodes if node.id in replacement_ids
        }
        original_connections = {connection.id: connection for connection in original.connections}
        replacement_connections = tuple(
            connection
            for connection in randomized.connections
            if original_connections.get(connection.id) != connection
        )
        self.push(
            RandomizeNodesCommand(
                self.document,
                node_ids,
                replacement_nodes,
                replacement_connections,
                self._command_change_callback,
            )
        )
        return replacement_ids

    def relink_media(self, node_id: UUID, candidate: str | Path) -> None:
        self.push(
            RelinkMediaCommand(
                self.document,
                node_id,
                str(Path(candidate).expanduser().resolve()),
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

    def compatibilities(
        self,
        candidates: tuple[tuple[UUID, str, UUID, str], ...],
    ) -> dict[tuple[UUID, str, UUID, str], bool]:
        results = self.compiler.connection_compatibilities(self.document.snapshot(), candidates)
        return {endpoint: result.accepted for endpoint, result in results.items()}

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
                # input_ports also yields the effective variadic sockets
                # (e.g. Statistics values_1); enumerating fixed inputs only
                # would offer such nodes solely through connectable
                # parameters, wiring the source into the wrong socket.
                candidates = (
                    *(port.id for port in definition.input_ports(include_next_variadic=True)),
                    *(parameter.id for parameter in definition.parameters if parameter.connectable),
                )
                pairs = ((node_id, port_id, candidate_id, candidate) for candidate in candidates)
            else:
                pairs = (
                    (candidate_id, output.id, node_id, port_id) for output in definition.outputs
                )
            endpoints = tuple(pairs)
            compatibilities = self.compiler.connection_compatibilities(
                temporary.snapshot(), endpoints
            )
            for endpoint in endpoints:
                if compatibilities[endpoint].accepted:
                    results.append((definition, endpoint[3] if is_output else endpoint[1]))
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
        self.undo_stack.beginMacro(tr("Insert and connect node"))
        try:
            node_id = self.add_node(definition.type_id, position)
            if existing_is_output:
                self.add_connection(existing_node_id, existing_port_id, node_id, new_port_id)
            else:
                self.add_connection(node_id, new_port_id, existing_node_id, existing_port_id)
        finally:
            self.undo_stack.endMacro()
        return node_id

    def _refresh(self, *, runtime_changed: bool = True) -> None:
        self._compilation = self.compiler.compile(self.document.snapshot())
        self.report = self._compilation.report
        self.validationChanged.emit(self.report)
        self.changed.emit()
        if runtime_changed:
            self.runtimeChanged.emit()

    @Slot(bool)
    def _on_clean_changed(self, clean: bool) -> None:
        self.dirtyChanged.emit(not clean)


def _visualizer_type_ids_for(type_id: str) -> frozenset[str]:
    if type_id in _IMAGE_VISUALIZER_TYPE_IDS:
        return _IMAGE_VISUALIZER_TYPE_IDS
    if type_id in _NOTE_VISUALIZER_TYPE_IDS:
        return _NOTE_VISUALIZER_TYPE_IDS
    return frozenset()
