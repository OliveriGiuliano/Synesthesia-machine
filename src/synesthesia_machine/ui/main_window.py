"""Phase 2 main-window shell and document-lifecycle orchestration."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import cast

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QSettings, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
)

from synesthesia_machine import __version__
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.persistence import (
    GraphPersistenceError,
    fragment_from_json,
    fragment_to_json,
    load_graph,
)
from synesthesia_machine.persistence.autosave import AutosaveStore, RecoveryRecord
from synesthesia_machine.ui.actions import ActionRegistry, ActionSpec
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME, Theme
from synesthesia_machine.ui.view_models import PortViewModel
from synesthesia_machine.ui.widgets import (
    InspectorPanel,
    NodeLibrary,
    NodeSearchDialog,
    SearchCandidate,
)

CLIPBOARD_MIME_TYPE = "application/x-synesthesia-graph-fragment+json"
GRAPH_FILE_FILTER = "Synesthesia Machine Graph (*.synmachine.json *.json)"
_MAX_RECENT_FILES = 8


class _ReplacementDecision(Enum):
    PROCEED = auto()
    DISCARD = auto()
    CANCEL = auto()


def _clipboard_mime_data() -> QMimeData | None:
    """Normalize platform backends that return ``None`` for an empty clipboard."""
    mime_data_provider: Callable[[], object] = QApplication.clipboard().mimeData
    return cast("QMimeData | None", mime_data_provider())


class MainWindow(QMainWindow):
    """Compose the visual editor over one document-owned undo stack."""

    def __init__(
        self,
        registry: NodeRegistry,
        paths: ApplicationPaths,
        *,
        theme: Theme = DEFAULT_THEME,
        settings: QSettings | None = None,
        offer_recovery: bool = True,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.paths = paths
        self.theme = theme
        self.settings = settings or QSettings("Synesthesia Machine", "Synesthesia Machine")
        self.session = DocumentSession(registry, self)
        self.autosave_store = AutosaveStore(paths.recovery)
        self.action_registry = ActionRegistry(self)
        self.scene = GraphScene(self.session, theme, self)
        self.view = GraphView(self.scene, theme)
        self.library = NodeLibrary(registry, self)
        self.inspector = InspectorPanel(self.session, self)
        self.recent_menu = QMenu("Open &Recent", self)
        self._recent_paths = self._load_recent_paths()
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(60_000)
        self._node_count = QLabel(self)
        self._validation_status = QLabel(self)

        self.setObjectName("main_window")
        self.setAccessibleName("Synesthesia Machine graph editor")
        self.setWindowTitle("Untitled[*] — Synesthesia Machine")
        self.setWindowModified(False)
        self.resize(1280, 760)
        self.setMinimumSize(900, 560)
        self.setStyleSheet(theme.style_sheet())
        self.setCentralWidget(self.view)
        self._create_docks()
        self._create_actions()
        self._create_menus()
        self._create_status_bar()
        self._connect_signals()
        self._restore_window_state()
        self._refresh_recent_menu()
        self._refresh_document_ui()
        self._refresh_selection_ui()
        if offer_recovery:
            QTimer.singleShot(0, self._offer_recovery)

    def _create_docks(self) -> None:
        self.library_dock = QDockWidget("Node Library", self)
        self.library_dock.setObjectName("node_library_dock")
        self.library_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.library_dock.setWidget(self.library)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)

        self.inspector_dock = QDockWidget("Inspector", self)
        self.inspector_dock.setObjectName("inspector_dock")
        self.inspector_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.inspector_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock)

    def _create_actions(self) -> None:
        create = self.action_registry.create
        create(
            ActionSpec("new", "&New", "Create a new graph", QKeySequence.StandardKey.New),
            self.new_document,
        )
        create(
            ActionSpec("open", "&Open…", "Open a graph", QKeySequence.StandardKey.Open),
            self.open_document,
        )
        create(
            ActionSpec("save", "&Save", "Save the graph", QKeySequence.StandardKey.Save),
            self.save_document,
        )
        create(
            ActionSpec(
                "save_as",
                "Save &As…",
                "Save the graph to a new path",
                QKeySequence.StandardKey.SaveAs,
            ),
            self.save_document_as,
        )
        create(ActionSpec("exit", "E&xit", "Close Synesthesia Machine", "Ctrl+Q"), self.close)

        undo = self.session.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.action_registry.register("undo", undo, status_tip="Undo the last graph edit")
        redo = self.session.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.action_registry.register("redo", redo, status_tip="Redo the last graph edit")
        create(
            ActionSpec("copy", "&Copy", "Copy selected nodes", QKeySequence.StandardKey.Copy),
            self.copy_selection,
        )
        create(
            ActionSpec("paste", "&Paste", "Paste graph fragment", QKeySequence.StandardKey.Paste),
            self.paste_selection,
        )
        create(
            ActionSpec("duplicate", "&Duplicate", "Duplicate selected nodes", "Ctrl+D"),
            self.duplicate_selection,
        )
        create(
            ActionSpec("delete", "&Delete", "Delete selected nodes and cables", "Delete"),
            self.scene.delete_selection,
        )
        create(
            ActionSpec(
                "select_all", "Select &All", "Select all nodes", QKeySequence.StandardKey.SelectAll
            ),
            self.select_all_nodes,
        )
        create(
            ActionSpec("frame_selection", "Frame &Selection", "Frame selected graph objects", "F"),
            self.view.frame_selection,
        )
        create(
            ActionSpec("frame_all", "Frame &All", "Frame the complete graph", "Home"),
            self.view.frame_all,
        )
        create(
            ActionSpec("add_node", "&Add Node…", "Search for a node to add", "Ctrl+Space"),
            self.search_at_center,
        )
        self.action_registry.register(
            "toggle_library",
            self.library_dock.toggleViewAction(),
            status_tip="Show or hide the node library",
        )
        self.action_registry.register(
            "toggle_inspector",
            self.inspector_dock.toggleViewAction(),
            status_tip="Show or hide the inspector",
        )

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.action_registry.require("new"))
        file_menu.addAction(self.action_registry.require("open"))
        file_menu.addMenu(self.recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.action_registry.require("save"))
        file_menu.addAction(self.action_registry.require("save_as"))
        file_menu.addSeparator()
        file_menu.addAction(self.action_registry.require("exit"))

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.action_registry.require("undo"))
        edit_menu.addAction(self.action_registry.require("redo"))
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_registry.require("copy"))
        edit_menu.addAction(self.action_registry.require("paste"))
        edit_menu.addAction(self.action_registry.require("duplicate"))
        edit_menu.addAction(self.action_registry.require("delete"))
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_registry.require("select_all"))

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.action_registry.require("frame_selection"))
        view_menu.addAction(self.action_registry.require("frame_all"))
        view_menu.addSeparator()
        view_menu.addAction(self.action_registry.require("toggle_library"))
        view_menu.addAction(self.action_registry.require("toggle_inspector"))

        graph_menu = self.menuBar().addMenu("&Graph")
        graph_menu.addAction(self.action_registry.require("add_node"))

    def _create_status_bar(self) -> None:
        self.statusBar().showMessage("Engine stopped")
        self.statusBar().addPermanentWidget(self._validation_status)
        self.statusBar().addPermanentWidget(self._node_count)

    def _connect_signals(self) -> None:
        self.session.changed.connect(self._refresh_document_ui)
        self.session.changed.connect(self._schedule_autosave)
        self.session.pathChanged.connect(self._refresh_document_ui)
        self.session.dirtyChanged.connect(self._on_dirty_changed)
        self.scene.selectionChanged.connect(self._refresh_selection_ui)
        self.scene.connectionDroppedOnEmpty.connect(self._search_compatible_node)
        self.view.requestSearch.connect(self._search_nodes)
        self.library.nodeActivated.connect(self._add_library_node)
        self._autosave_timer.timeout.connect(self._autosave)
        QApplication.clipboard().dataChanged.connect(self._refresh_action_states)

    @Slot()
    def new_document(self) -> None:
        previous_id = self.session.document.document_id
        decision = self._confirm_document_replacement()
        if decision is _ReplacementDecision.CANCEL:
            return
        self.session.new_document()
        if decision is _ReplacementDecision.DISCARD:
            self.autosave_store.discard(previous_id)
        self.statusBar().showMessage("Created new graph", 3000)

    @Slot()
    def open_document(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Open Graph", "", GRAPH_FILE_FILTER)
        if selected:
            self.open_path(Path(selected))

    def open_path(self, path: Path) -> bool:
        previous_id = self.session.document.document_id
        decision = self._confirm_document_replacement()
        if decision is _ReplacementDecision.CANCEL:
            return False
        try:
            self.session.open_document(path)
        except (GraphPersistenceError, OSError, ValueError) as error:
            self._show_error("Could not open graph", str(error))
            return False
        if decision is _ReplacementDecision.DISCARD:
            self.autosave_store.discard(previous_id)
        self._remember_recent(path)
        self.statusBar().showMessage(f"Opened {path.name}", 4000)
        return True

    @Slot()
    def save_document(self) -> bool:
        if self.session.current_path is None:
            return self.save_document_as()
        return self._save_to_path(self.session.current_path)

    @Slot()
    def save_document_as(self) -> bool:
        initial = str(self.session.current_path or Path.home() / "Untitled.synmachine.json")
        selected, _ = QFileDialog.getSaveFileName(self, "Save Graph", initial, GRAPH_FILE_FILTER)
        if not selected:
            return False
        path = Path(selected)
        if not path.suffix:
            path = path.with_suffix(".synmachine.json")
        return self._save_to_path(path)

    def _save_to_path(self, path: Path) -> bool:
        document_id = self.session.document.document_id
        try:
            saved = self.session.save(path)
        except (OSError, ValueError) as error:
            self._show_error("Could not save graph", str(error))
            return False
        self.autosave_store.discard(document_id)
        self._remember_recent(saved)
        self.statusBar().showMessage(f"Saved {saved.name}", 4000)
        return True

    @Slot()
    def copy_selection(self) -> None:
        node_ids = self.scene.selected_node_ids()
        if not node_ids:
            return
        text = fragment_to_json(self.session.copy(node_ids))
        payload = QMimeData()
        payload.setData(CLIPBOARD_MIME_TYPE, QByteArray(text.encode("utf-8")))
        payload.setText(text)
        QApplication.clipboard().setMimeData(payload)
        self.statusBar().showMessage(f"Copied {len(node_ids)} node(s)", 2500)

    @Slot()
    def paste_selection(self) -> None:
        mime_data = _clipboard_mime_data()
        if mime_data is None or not mime_data.hasFormat(CLIPBOARD_MIME_TYPE):
            return
        raw = mime_data.data(CLIPBOARD_MIME_TYPE).data()
        payload = raw.tobytes() if isinstance(raw, memoryview) else raw
        try:
            fragment = fragment_from_json(payload.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            self._show_error("Could not paste graph fragment", str(error))
            return
        node_ids = self.session.paste(fragment)
        self.scene.select_node_ids(node_ids)
        self.statusBar().showMessage(f"Pasted {len(node_ids)} node(s)", 2500)

    @Slot()
    def duplicate_selection(self) -> None:
        selected = self.scene.selected_node_ids()
        if not selected:
            return
        node_ids = self.session.duplicate(selected)
        self.scene.select_node_ids(node_ids)

    @Slot()
    def select_all_nodes(self) -> None:
        self.scene.select_node_ids(set(self.scene.node_items))

    @Slot()
    def search_at_center(self) -> None:
        self._search_nodes(self.view.mapToScene(self.view.viewport().rect().center()))

    @Slot(object)
    def _search_nodes(self, value: object) -> None:
        if not isinstance(value, QPointF):
            return
        candidates = (SearchCandidate(definition) for definition in self.registry.definitions())
        dialog = NodeSearchDialog(candidates, self, title="Add Node")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_candidate()
        if selected is not None:
            node_id = self.session.add_node(
                selected.definition.type_id,
                (value.x(), value.y()),
            )
            self.scene.select_node_ids({node_id})

    @Slot(object, object)
    def _search_compatible_node(self, port_value: object, position_value: object) -> None:
        if not isinstance(port_value, PortViewModel) or not isinstance(position_value, QPointF):
            return
        compatible = self.session.compatible_definitions(
            port_value.node_id,
            port_value.port_id,
            port_value.is_output,
        )
        candidates = tuple(
            SearchCandidate(definition, port_id) for definition, port_id in compatible
        )
        if not candidates:
            self.statusBar().showMessage("No compatible node types are registered", 3500)
            return
        dialog = NodeSearchDialog(candidates, self, title="Add Compatible Node")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_candidate()
        if selected is None or selected.port_id is None:
            return
        node_id = self.session.insert_and_connect(
            selected.definition,
            selected.port_id,
            port_value.node_id,
            port_value.port_id,
            port_value.is_output,
            (position_value.x(), position_value.y()),
        )
        self.scene.select_node_ids({node_id})

    @Slot(str)
    def _add_library_node(self, type_id: str) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node_id = self.session.add_node(type_id, (center.x(), center.y()))
        self.scene.select_node_ids({node_id})

    @Slot()
    def _refresh_document_ui(self) -> None:
        path = self.session.current_path
        name = path.name if path is not None else "Untitled"
        self.setWindowTitle(f"{name}[*] — Synesthesia Machine {__version__}")
        self.setWindowModified(self.session.is_dirty)
        snapshot = self.session.document.snapshot()
        self._node_count.setText(
            f"{len(snapshot.nodes)} node(s) · {len(snapshot.connections)} cable(s)"
        )
        errors = len(self.session.report.errors)
        warnings = len(self.session.report.warnings)
        self._validation_status.setText(f"{errors} error(s) · {warnings} warning(s)")
        self._refresh_action_states()

    @Slot()
    def _refresh_selection_ui(self) -> None:
        self.inspector.set_selection(
            self.scene.selected_node_ids(),
            self.scene.selected_connection_ids(),
        )
        self._refresh_action_states()

    @Slot()
    def _refresh_action_states(self) -> None:
        has_nodes = bool(self.scene.selected_node_ids())
        has_selection = has_nodes or bool(self.scene.selected_connection_ids())
        self.action_registry.require("copy").setEnabled(has_nodes)
        self.action_registry.require("duplicate").setEnabled(has_nodes)
        self.action_registry.require("delete").setEnabled(has_selection)
        mime_data = _clipboard_mime_data()
        self.action_registry.require("paste").setEnabled(
            mime_data is not None and mime_data.hasFormat(CLIPBOARD_MIME_TYPE)
        )
        self.action_registry.require("frame_selection").setEnabled(has_selection)

    @Slot(bool)
    def _on_dirty_changed(self, dirty: bool) -> None:
        self.setWindowModified(dirty)
        if dirty:
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()

    @Slot()
    def _schedule_autosave(self) -> None:
        if self.session.is_dirty:
            self._autosave_timer.start()

    @Slot()
    def _autosave(self) -> None:
        if not self.session.is_dirty:
            return
        try:
            path = self.autosave_store.save(self.session.document.snapshot())
        except OSError as error:
            self.statusBar().showMessage(f"Autosave failed: {error}", 5000)
            return
        self.statusBar().showMessage(f"Recovery saved to {path.name}", 3500)

    def _confirm_document_replacement(self) -> _ReplacementDecision:
        if not self.session.is_dirty:
            return _ReplacementDecision.PROCEED
        self._autosave()
        choice = QMessageBox.warning(
            self,
            "Unsaved graph",
            "Save changes before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice is QMessageBox.StandardButton.Save:
            return (
                _ReplacementDecision.PROCEED
                if self.save_document()
                else _ReplacementDecision.CANCEL
            )
        if choice is QMessageBox.StandardButton.Discard:
            return _ReplacementDecision.DISCARD
        return _ReplacementDecision.CANCEL

    @Slot()
    def _offer_recovery(self) -> None:
        for record in self.autosave_store.discover():
            explicit_path = self._explicit_path_for(record)
            if not record.is_newer_than(explicit_path):
                continue
            choice = QMessageBox.warning(
                self,
                "Recover autosaved graph",
                "A newer autosave was found. Restore it now?\n\n"
                f"Recovery: {record.path.name}"
                + (f"\nOriginal: {explicit_path}" if explicit_path is not None else ""),
                QMessageBox.StandardButton.Open
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Open,
            )
            if choice is QMessageBox.StandardButton.Discard:
                self.autosave_store.discard(record.document_id)
                continue
            if choice is not QMessageBox.StandardButton.Open:
                return
            try:
                self.session.recover_document(record.path, explicit_path=explicit_path)
            except (GraphPersistenceError, OSError, ValueError) as error:
                self._show_error("Could not recover graph", str(error))
                return
            self.statusBar().showMessage(f"Recovered {record.path.name}", 5000)
            return

    def _explicit_path_for(self, record: RecoveryRecord) -> Path | None:
        for path in self._recent_paths:
            try:
                snapshot = load_graph(path, self.registry)
            except (GraphPersistenceError, OSError, ValueError):
                continue
            if snapshot.document_id == record.document_id:
                return path
        return None

    def _load_recent_paths(self) -> list[Path]:
        raw: object = self.settings.value("recentFiles", [])
        if not isinstance(raw, list):
            return []
        values = cast(list[object], raw)
        return [Path(value) for value in values if isinstance(value, str) and Path(value).exists()]

    def _remember_recent(self, path: Path) -> None:
        resolved = path.resolve()
        self._recent_paths = [resolved, *(item for item in self._recent_paths if item != resolved)]
        self._recent_paths = self._recent_paths[:_MAX_RECENT_FILES]
        self.settings.setValue("recentFiles", [str(item) for item in self._recent_paths])
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        if not self._recent_paths:
            empty = QAction("No recent graphs", self.recent_menu)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for index, path in enumerate(self._recent_paths, start=1):
            action = QAction(f"&{index} {path.name}", self.recent_menu)
            action.setToolTip(str(path))
            action.triggered.connect(partial(self.open_path, path))
            self.recent_menu.addAction(action)

    def _restore_window_state(self) -> None:
        geometry = cast(object, self.settings.value("windowGeometry"))
        state = cast(object, self.settings.value("windowState"))
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        if isinstance(state, QByteArray):
            self.restoreState(state)

    def _save_window_state(self) -> None:
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.sync()

    def _show_error(self, title: str, detail: str) -> None:
        QMessageBox.critical(self, title, detail)
        self.statusBar().showMessage(detail, 5000)

    def closeEvent(self, event: QCloseEvent) -> None:
        decision = self._confirm_document_replacement()
        if decision is not _ReplacementDecision.CANCEL:
            if decision is _ReplacementDecision.DISCARD:
                self.autosave_store.discard(self.session.document.document_id)
            self._save_window_state()
            event.accept()
            return
        event.ignore()
