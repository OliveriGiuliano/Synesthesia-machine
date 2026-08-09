"""Registry-backed node discovery and snapshot-backed inspector widgets."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from uuid import UUID

from PySide6.QtCore import QByteArray, QMimeData, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDrag, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synesthesia_machine.contracts import NodeMemoryDiagnostic
from synesthesia_machine.graph import (
    LiteralValue,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from synesthesia_machine.nodes import NodeDefinition, NodeRegistry
from synesthesia_machine.ui.canvas import NODE_MIME_TYPE
from synesthesia_machine.ui.musical_controls import MusicalParameterEditor
from synesthesia_machine.ui.parameter_editors import create_parameter_editor, parameter_tooltip
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import node_category_color
from synesthesia_machine.ui.tooltips import format_tooltip
from synesthesia_machine.ui.view_models import ConnectionViewModel, NodeViewModel

_TYPE_ROLE = int(Qt.ItemDataRole.UserRole)
_INDEX_ROLE = _TYPE_ROLE + 1
_ISSUE_ROLE = _INDEX_ROLE + 1
_CATEGORY_ROLE = _ISSUE_ROLE + 1
_GROUP_DEPTH_ROLE = _CATEGORY_ROLE + 1

_LIBRARY_ROOT_ORDER = {
    "Input": 0,
    "Image": 1,
    "Synesthesia": 2,
    "Output": 3,
    "Visualization": 4,
    "Utility": 5,
}
_LIBRARY_ROOT_LABELS = {"Input": "Inputs", "Output": "Outputs"}
_LIBRARY_SUBGROUP_LABELS = {
    "Adjustment": "Adjustments",
    "Channel": "Channels",
    "Dimension": "Dimensions",
    "Filter": "Filters",
    "Utility": "Utilities",
}


class ValidationIssuePanel(QWidget):
    """Always-visible compiler issue summary with navigation requests."""

    issueActivated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = QLabel("0 errors · 0 warnings", self)
        self.summary.setAccessibleName("Graph validation summary")
        self.issues = QListWidget(self)
        self.issues.setAccessibleName("Graph validation issue list")
        self.issues.itemClicked.connect(self._activate_issue)
        self.issues.itemActivated.connect(self._activate_issue)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.summary)
        layout.addWidget(self.issues, 1)
        self.set_report(ValidationReport())

    @Slot(object)
    def set_report(self, report: ValidationReport) -> None:
        self.issues.clear()
        self.summary.setText(f"{len(report.errors)} errors · {len(report.warnings)} warnings")
        for issue in report.issues:
            item = QListWidgetItem(f"[{issue.severity}] {issue.message}")
            item.setData(_ISSUE_ROLE, issue)
            item.setToolTip(_issue_detail(issue))
            self.issues.addItem(item)
        if not report.issues:
            item = QListWidgetItem("Graph is valid")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.issues.addItem(item)

    @Slot(QListWidgetItem)
    def _activate_issue(self, item: QListWidgetItem) -> None:
        issue = item.data(_ISSUE_ROLE)
        if isinstance(issue, ValidationIssue):
            self.issueActivated.emit(issue)


class NodeTreeWidget(QTreeWidget):
    """Tree that exports stable node type IDs through the editor MIME contract."""

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        del supported_actions
        selected = self.selectedItems()
        if not selected:
            return
        item = selected[0]
        value = item.data(0, _TYPE_ROLE)
        if not isinstance(value, str):
            return
        mime_data = QMimeData()
        mime_data.setData(NODE_MIME_TYPE, QByteArray(value.encode("utf-8")))
        mime_data.setText(value)
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)


class NodeLibrary(QWidget):
    """Searchable, ordered category/subcategory projection of registry definitions."""

    nodeActivated = Signal(str)

    def __init__(self, registry: NodeRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.search = QLineEdit(self)
        self.search.setObjectName("node_library_search")
        self.search.setAccessibleName("Search node library")
        self.search.setPlaceholderText("Search nodes…")
        self.tree = NodeTreeWidget(self)
        self.tree.setObjectName("node_library_tree")
        self.tree.setAccessibleName("Node library")
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setIndentation(18)
        self.tree.setAnimated(True)
        self.tree.setRootIsDecorated(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)
        self.search.textChanged.connect(self._populate)
        self.tree.itemDoubleClicked.connect(self._activate_item)
        self.tree.itemActivated.connect(self._activate_item)
        self._populate("")

    @Slot(str)
    def _populate(self, query: str) -> None:
        selected = self.selected_type_id()
        self.tree.clear()
        groups: dict[tuple[str, ...], QTreeWidgetItem] = {}
        normalized = query.strip().casefold()
        definitions = sorted(self.registry.definitions(), key=_library_sort_key)
        for definition in definitions:
            haystack = _definition_search_text(definition)
            if normalized and not all(token in haystack for token in normalized.split()):
                continue
            category_path = _category_path(definition.category)
            parent: QTreeWidgetItem | None = None
            for depth in range(1, len(category_path) + 1):
                path = category_path[:depth]
                group = groups.get(path)
                if group is None:
                    label = _library_group_label(path)
                    group = (
                        QTreeWidgetItem(self.tree, [label])
                        if parent is None
                        else QTreeWidgetItem(parent, [label])
                    )
                    _style_library_group(group, " / ".join(path), depth - 1)
                    groups[path] = group
                parent = group
            assert parent is not None
            item = QTreeWidgetItem(parent, [definition.display_name])
            item.setForeground(0, QBrush(node_category_color(definition.category)))
            item.setData(0, _TYPE_ROLE, definition.type_id)
            item.setData(0, _CATEGORY_ROLE, definition.category)
            item.setToolTip(0, format_tooltip(definition.description))
            item.setStatusTip(0, definition.description)
            if definition.type_id == selected:
                self.tree.setCurrentItem(item)
        self.tree.expandAll()

    @Slot(QTreeWidgetItem, int)
    def _activate_item(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        value = item.data(0, _TYPE_ROLE)
        if isinstance(value, str):
            self.nodeActivated.emit(value)

    def selected_type_id(self) -> str | None:
        selected = self.tree.selectedItems()
        if not selected:
            return None
        item = selected[0]
        value = item.data(0, _TYPE_ROLE)
        return value if isinstance(value, str) else None


def _category_path(category: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in category.split("/") if part.strip())
    return parts or ("Other",)


def _library_group_label(path: tuple[str, ...]) -> str:
    name = path[-1]
    if len(path) == 1:
        return _LIBRARY_ROOT_LABELS.get(name, name)
    return _LIBRARY_SUBGROUP_LABELS.get(name, name)


def _library_sort_key(definition: NodeDefinition) -> tuple[object, ...]:
    path = _category_path(definition.category)
    root = path[0]
    return (
        _LIBRARY_ROOT_ORDER.get(root, len(_LIBRARY_ROOT_ORDER)),
        root.casefold(),
        *(part.casefold() for part in path[1:]),
        definition.display_name.casefold(),
    )


def _style_library_group(item: QTreeWidgetItem, category: str, depth: int) -> None:
    color = node_category_color(category)
    background = QColor(color)
    background.setAlpha(52 if depth == 0 else 30)
    font = item.font(0)
    font.setBold(True)
    if depth == 0:
        font.setPointSizeF(font.pointSizeF() + 0.75)
    item.setFont(0, font)
    item.setForeground(0, QBrush(color))
    item.setBackground(0, QBrush(background))
    item.setSizeHint(0, QSize(0, 29 if depth == 0 else 25))
    item.setData(0, _CATEGORY_ROLE, category)
    item.setData(0, _GROUP_DEPTH_ROLE, depth)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    definition: NodeDefinition
    port_id: str | None = None


class NodeSearchDialog(QDialog):
    """Keyboard-first fuzzy-ish search over a pre-filtered candidate set."""

    def __init__(
        self,
        candidates: Iterable[SearchCandidate],
        parent: QWidget | None = None,
        *,
        title: str = "Add Node",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(480, 420)
        self._candidates = tuple(candidates)
        self.search = QLineEdit(self)
        self.search.setObjectName("graph_search_field")
        self.search.setAccessibleName("Search graph nodes")
        self.search.setPlaceholderText("Type a node name, category, or keyword…")
        self.results = QListWidget(self)
        self.results.setObjectName("graph_search_results")
        self.results.setAccessibleName("Graph search results")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.results)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._populate)
        self.results.itemDoubleClicked.connect(self._accept_item)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        self._populate("")
        self.search.setFocus()

    @Slot(str)
    def _populate(self, query: str) -> None:
        self.results.clear()
        normalized = query.strip().casefold()
        for index, candidate in enumerate(self._candidates):
            definition = candidate.definition
            haystack = _definition_search_text(definition)
            if normalized and not all(token in haystack for token in normalized.split()):
                continue
            suffix = f"  ·  {candidate.port_id}" if candidate.port_id is not None else ""
            item = QListWidgetItem(f"{definition.display_name}  —  {definition.category}{suffix}")
            item.setData(_INDEX_ROLE, index)
            item.setToolTip(format_tooltip(definition.description))
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    @Slot(QListWidgetItem)
    def _accept_item(self, item: QListWidgetItem) -> None:
        self.results.setCurrentItem(item)
        self._accept_current()

    @Slot()
    def _accept_current(self) -> None:
        if self.results.selectedItems():
            self.accept()

    def selected_candidate(self) -> SearchCandidate | None:
        selected = self.results.selectedItems()
        if not selected:
            return None
        item = selected[0]
        index = item.data(_INDEX_ROLE)
        return self._candidates[index] if isinstance(index, int) else None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_current()
            event.accept()
            return
        super().keyPressEvent(event)


class InspectorPanel(QWidget):
    """Render selection metadata, scalar editors, and compiler diagnostics."""

    def __init__(self, session: DocumentSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspector_panel")
        self.session = session
        self._node_ids: set[UUID] = set()
        self._connection_ids: set[UUID] = set()
        self._memory_diagnostic: NodeMemoryDiagnostic | None = None
        self.title = QLabel("Nothing selected", self)
        title_font = self.title.font()
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.description = QLabel("Select a node or cable to inspect it.", self)
        self.description.setWordWrap(True)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("inspector_scroll_area")
        self.scroll_area.viewport().setObjectName("inspector_scroll_viewport")
        self.scroll_area.setWidgetResizable(True)
        self.form_container = QWidget(self.scroll_area)
        self.form_container.setObjectName("inspector_form_container")
        self.form = QFormLayout(self.form_container)
        self.scroll_area.setWidget(self.form_container)
        self.validation_title = QLabel("Validation", self)
        self.validation = QListWidget(self)
        self.validation.setAccessibleName("Validation issues")
        self.validation.setMinimumHeight(110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.validation_title)
        layout.addWidget(self.validation)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)
        session.changed.connect(self._schedule_refresh)
        self.refresh()

    @Slot()
    def _schedule_refresh(self) -> None:
        """Refresh after the active editor has finished dispatching its Qt signal."""

        self._refresh_timer.start()

    def set_selection(self, node_ids: set[UUID], connection_ids: set[UUID]) -> None:
        self._node_ids = set(node_ids)
        self._connection_ids = set(connection_ids)
        if self._memory_diagnostic is not None and self._memory_diagnostic.node_id not in node_ids:
            self._memory_diagnostic = None
        self.refresh()

    def set_memory_diagnostic(self, diagnostic: NodeMemoryDiagnostic | None) -> None:
        if diagnostic == self._memory_diagnostic:
            return
        self._memory_diagnostic = diagnostic
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        self._refresh_timer.stop()
        self._clear_form()
        view_model = self.session.view_model
        nodes = tuple(node for node in view_model.nodes if node.node_id in self._node_ids)
        connections = tuple(
            connection
            for connection in view_model.connections
            if connection.connection_id in self._connection_ids
        )
        if len(nodes) == 1 and not connections:
            self._show_node(nodes[0])
        elif len(connections) == 1 and not nodes:
            self._show_connection(connections[0])
        elif nodes or connections:
            self.title.setText("Multiple selection")
            self.description.setText(
                f"{len(nodes)} node(s) and {len(connections)} cable(s) selected."
            )
            self._show_issues(())
        else:
            self.title.setText("Nothing selected")
            self.description.setText("Select a node or cable to inspect it.")
            self._show_issues(self.session.report.issues)

    def _show_node(self, node: NodeViewModel) -> None:
        self.title.setText(node.title)
        self.description.setText(node.description)
        self.form.addRow("Type", QLabel(node.type_id, self.form_container))
        self.form.addRow("Category", QLabel(node.category, self.form_container))
        grouped_parameter_ids = {
            parameter.spec.id for group in node.parameter_groups for parameter in group.parameters
        }
        for group in node.parameter_groups:
            editor = MusicalParameterEditor(
                group,
                partial(self._set_parameter, node.node_id),
                self.form_container,
            )
            self.form.addRow(editor)
        for parameter in node.parameters:
            if parameter.spec.id in grouped_parameter_ids:
                continue
            callback = partial(self._set_parameter, node.node_id, parameter.spec.id)
            editor = create_parameter_editor(parameter, callback)
            label = QLabel(parameter.spec.label, self.form_container)
            help_text = parameter_tooltip(parameter.spec)
            label.setToolTip(format_tooltip(help_text))
            label.setAccessibleDescription(help_text)
            self.form.addRow(label, editor)
            if parameter.spec.help_text:
                help_label = QLabel(help_text, self.form_container)
                help_label.setObjectName(f"parameter_help_{parameter.spec.id}")
                help_label.setWordWrap(True)
                help_label.setStyleSheet("color: #9aa6b2; font-size: 8pt;")
                self.form.addRow("", help_label)
        diagnostic = self._memory_diagnostic
        if diagnostic is not None and diagnostic.node_id == node.node_id:
            self.form.addRow(
                "Estimated retained memory",
                QLabel(_format_bytes(diagnostic.estimated_retained_bytes), self.form_container),
            )
            self.form.addRow(
                "Current retained memory",
                QLabel(
                    f"{_format_bytes(diagnostic.retained_bytes)} "
                    f"({diagnostic.retained_frame_count}/{diagnostic.capacity_frame_count} frames)",
                    self.form_container,
                ),
            )
            self.form.addRow(
                "Memory limit",
                QLabel(_format_bytes(diagnostic.memory_limit_bytes), self.form_container),
            )
        self._show_issues(node.issues)

    def _show_connection(self, connection: ConnectionViewModel) -> None:
        self.title.setText("Connection")
        self.description.setText(f"{connection.source_port_id} → {connection.destination_port_id}")
        self.form.addRow("Resolved type", QLabel(connection.type_name, self.form_container))
        self.form.addRow("Source node", QLabel(str(connection.source_node_id), self.form_container))
        self.form.addRow(
            "Destination node", QLabel(str(connection.destination_node_id), self.form_container)
        )
        self._show_issues(connection.issues)

    def _show_issues(self, issues: Iterable[object]) -> None:
        self.validation.clear()
        count = 0
        for issue in issues:
            severity = getattr(issue, "severity", "ISSUE")
            message = getattr(issue, "message", str(issue))
            code = getattr(issue, "code", "")
            self.validation.addItem(f"{severity}: {message} ({code})")
            count += 1
        if count == 0:
            self.validation.addItem("No validation issues")

    def _set_parameter(self, node_id: UUID, parameter_id: str, value: LiteralValue) -> None:
        self.session.set_parameter(node_id, parameter_id, value)

    def _clear_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)


def _definition_search_text(definition: NodeDefinition) -> str:
    return " ".join(
        (
            definition.display_name,
            definition.category,
            definition.description,
            definition.type_id,
            *definition.aliases,
        )
    ).casefold()


def _format_bytes(value: int) -> str:
    return f"{value / (1024 * 1024):.2f} MiB"


def _issue_detail(issue: ValidationIssue) -> str:
    details = [f"{issue.severity}: {issue.message}", f"Code: {issue.code}"]
    if issue.node_id is not None:
        details.append(f"Node: {issue.node_id}")
    if issue.connection_id is not None:
        details.append(f"Connection: {issue.connection_id}")
    if issue.port_id is not None:
        details.append(f"Port: {issue.port_id}")
    if issue.severity is ValidationSeverity.ERROR:
        details.append("This issue prevents activation of the current graph revision.")
    return "\n".join(details)
