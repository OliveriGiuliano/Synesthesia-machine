"""Graph editor, EngineClient transport controls, and runtime preview orchestration."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

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
    QToolBar,
)

from synesthesia_machine import __version__
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    EngineClient,
    EngineConnectionState,
    EngineStatus,
    SourceState,
)
from synesthesia_machine.graph import AlignMode, DistributionAxis, GroupKind, ValidationIssue
from synesthesia_machine.nodes import ExecutionKind, NodeRegistry
from synesthesia_machine.persistence import (
    GraphPersistenceError,
    RelinkMatch,
    find_missing_media,
    fragment_from_json,
    fragment_to_json,
    load_graph,
    verify_relink_candidate,
)
from synesthesia_machine.persistence.autosave import AutosaveStore, RecoveryRecord
from synesthesia_machine.ui.actions import ActionRegistry, ActionSpec
from synesthesia_machine.ui.application_settings import (
    ApplicationSettingsStore,
    EditorPreferences,
    PreferencesDialog,
)
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.previews import RuntimePreviewPanel
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME, Theme
from synesthesia_machine.ui.transport import resolve_transport_target
from synesthesia_machine.ui.view_models import PortViewModel
from synesthesia_machine.ui.widgets import (
    InspectorPanel,
    NodeLibrary,
    NodeSearchDialog,
    SearchCandidate,
    ValidationIssuePanel,
)

CLIPBOARD_MIME_TYPE = "application/x-synesthesia-graph-fragment+json"
GRAPH_FILE_FILTER = "Synesthesia Machine Graph (*.synmachine.json *.json)"


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
        engine_client: EngineClient,
        *,
        theme: Theme = DEFAULT_THEME,
        settings: QSettings | None = None,
        offer_recovery: bool = True,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.paths = paths
        self.engine_client = engine_client
        self.theme = theme
        self.settings = settings or QSettings("Synesthesia Machine", "Synesthesia Machine")
        self.settings_store = ApplicationSettingsStore(self.settings)
        self.preferences = self.settings_store.load_preferences()
        self.session = DocumentSession(registry, self)
        self.autosave_store = AutosaveStore(paths.recovery)
        self.action_registry = ActionRegistry(self)
        self.scene = GraphScene(self.session, theme, self)
        self.scene.configure_grid_snap(
            enabled=self.preferences.grid_snap_enabled,
            spacing=self.preferences.grid_size,
        )
        self.view = GraphView(self.scene, theme)
        self.library = NodeLibrary(registry, self)
        self.inspector = InspectorPanel(self.session, self)
        self.issues_panel = ValidationIssuePanel(self)
        self.preview_panel = RuntimePreviewPanel(self)
        self.recent_menu = QMenu("Open &Recent", self)
        self._recent_paths = self._load_recent_paths()
        self._image_sequences: dict[UUID, int] = {}
        self._note_sequences: dict[UUID, int] = {}
        self._engine_closed = False
        self._engine_failure_signature: tuple[object, ...] | None = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(self.preferences.autosave_delay_seconds * 1000)
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.setInterval(100)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(16)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(100)
        self._node_count = QLabel(self)
        self._validation_status = QLabel(self)
        self._engine_status = QLabel(self)

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
        self._create_toolbar()
        self._create_menus()
        self._create_status_bar()
        self._connect_signals()
        self._restore_window_state()
        self._refresh_recent_menu()
        self._refresh_document_ui()
        self._refresh_selection_ui()
        self._preview_timer.start()
        self._metrics_timer.start()
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

        self.preview_dock = QDockWidget("Runtime Previews", self)
        self.preview_dock.setObjectName("runtime_preview_dock")
        self.preview_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.preview_dock.setWidget(self.preview_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.preview_dock)

        self.issues_dock = QDockWidget("Validation Issues", self)
        self.issues_dock.setObjectName("validation_issues_dock")
        self.issues_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.issues_dock.setWidget(self.issues_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.issues_dock)

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
            ActionSpec(
                "relink_media",
                "Locate Missing &Media…",
                "Select a replacement file for a missing Load Video source",
            ),
            self.locate_missing_media,
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
        create(
            ActionSpec("preferences", "&Preferences…", "Edit autosave and canvas preferences"),
            self.edit_preferences,
        )

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
        create(
            ActionSpec("add_group", "Add &Group", "Add an organizational group to the canvas"),
            self.add_group_at_center,
        )
        create(
            ActionSpec("add_comment", "Add &Comment", "Add a free-form canvas comment"),
            self.add_comment_at_center,
        )
        for key, text, mode in (
            ("align_left", "Align &Left", AlignMode.LEFT),
            ("align_hcenter", "Align Horizontal &Centers", AlignMode.HORIZONTAL_CENTER),
            ("align_right", "Align &Right", AlignMode.RIGHT),
            ("align_top", "Align &Top", AlignMode.TOP),
            ("align_vcenter", "Align Vertical C&enters", AlignMode.VERTICAL_CENTER),
            ("align_bottom", "Align &Bottom", AlignMode.BOTTOM),
        ):
            create(
                ActionSpec(key, text, "Align selected nodes as one undoable command"),
                partial(self.scene.align_selection, mode),
            )
        create(
            ActionSpec(
                "distribute_horizontal",
                "Distribute &Horizontally",
                "Distribute selected nodes horizontally with equal gaps",
            ),
            partial(self.scene.distribute_selection, DistributionAxis.HORIZONTAL),
        )
        create(
            ActionSpec(
                "distribute_vertical",
                "Distribute &Vertically",
                "Distribute selected nodes vertically with equal gaps",
            ),
            partial(self.scene.distribute_selection, DistributionAxis.VERTICAL),
        )
        create(
            ActionSpec(
                "tidy_selection",
                "&Tidy Selection",
                "Arrange selected nodes into deterministic graph layers",
                "Ctrl+T",
            ),
            self.scene.tidy_selection,
        )
        create(ActionSpec("play", "&Play", "Play or resume the targeted source", "F5"), self.play)
        create(ActionSpec("pause", "P&ause", "Pause the targeted source", "F6"), self.pause)
        create(ActionSpec("stop", "&Stop", "Stop the targeted source", "F7"), self.stop)
        create(ActionSpec("reload", "&Reload", "Reload the targeted source", "F8"), self.reload)
        create(
            ActionSpec("panic", "&Panic", "Immediately silence debug audio", "Ctrl+Shift+Escape"),
            self.panic,
        )
        restart_engine = create(
            ActionSpec(
                "restart_engine",
                "Restart &Engine",
                "Restart the child engine and rebuild the latest valid runtime",
                "Ctrl+Shift+R",
            ),
            self.restart_engine,
        )
        restart_engine.setEnabled(False)
        create(ActionSpec("about", "&About", "About Synesthesia Machine"), self._show_about)
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
        self.action_registry.register(
            "toggle_previews",
            self.preview_dock.toggleViewAction(),
            status_tip="Show or hide runtime previews",
        )
        self.action_registry.register(
            "toggle_issues",
            self.issues_dock.toggleViewAction(),
            status_tip="Show or hide the graph validation issue list",
        )

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Transport", self)
        toolbar.setObjectName("transport_toolbar")
        toolbar.setAccessibleName("Source transport and panic")
        toolbar.setMovable(False)
        for key in ("play", "pause", "stop", "reload"):
            toolbar.addAction(self.action_registry.require(key))
        toolbar.addSeparator()
        toolbar.addAction(self.action_registry.require("panic"))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.transport_toolbar = toolbar

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.action_registry.require("new"))
        file_menu.addAction(self.action_registry.require("open"))
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(self.action_registry.require("relink_media"))
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
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_registry.require("preferences"))

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.action_registry.require("frame_selection"))
        view_menu.addAction(self.action_registry.require("frame_all"))
        view_menu.addSeparator()
        view_menu.addAction(self.action_registry.require("toggle_library"))
        view_menu.addAction(self.action_registry.require("toggle_inspector"))
        view_menu.addAction(self.action_registry.require("toggle_previews"))
        view_menu.addAction(self.action_registry.require("toggle_issues"))

        graph_menu = self.menuBar().addMenu("&Graph")
        graph_menu.addAction(self.action_registry.require("add_node"))
        graph_menu.addAction(self.action_registry.require("add_group"))
        graph_menu.addAction(self.action_registry.require("add_comment"))
        arrange_menu = graph_menu.addMenu("&Arrange Selection")
        for key in (
            "align_left",
            "align_hcenter",
            "align_right",
            "align_top",
            "align_vcenter",
            "align_bottom",
        ):
            arrange_menu.addAction(self.action_registry.require(key))
        arrange_menu.addSeparator()
        arrange_menu.addAction(self.action_registry.require("distribute_horizontal"))
        arrange_menu.addAction(self.action_registry.require("distribute_vertical"))
        arrange_menu.addSeparator()
        arrange_menu.addAction(self.action_registry.require("tidy_selection"))
        graph_menu.addSeparator()
        for key in ("play", "pause", "stop", "reload"):
            graph_menu.addAction(self.action_registry.require(key))
        graph_menu.addSeparator()
        graph_menu.addAction(self.action_registry.require("restart_engine"))

        midi_menu = self.menuBar().addMenu("&MIDI")
        midi_menu.addAction(self.action_registry.require("panic"))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.action_registry.require("about"))

    def _create_status_bar(self) -> None:
        self.statusBar().showMessage("Ready")
        self._engine_status.setText("Engine STOPPED · 0 ticks")
        self._engine_status.setAccessibleName("Engine runtime status")
        self.statusBar().addPermanentWidget(self._engine_status, 1)
        self.statusBar().addPermanentWidget(self._validation_status)
        self.statusBar().addPermanentWidget(self._node_count)

    def _connect_signals(self) -> None:
        self.session.changed.connect(self._refresh_document_ui)
        self.session.changed.connect(self._schedule_autosave)
        self.session.changed.connect(self._schedule_engine_activation)
        self.session.pathChanged.connect(self._refresh_document_ui)
        self.session.dirtyChanged.connect(self._on_dirty_changed)
        self.session.validationChanged.connect(self.issues_panel.set_report)
        self.issues_panel.issueActivated.connect(self._focus_validation_issue)
        self.scene.selectionChanged.connect(self._refresh_selection_ui)
        self.scene.connectionDroppedOnEmpty.connect(self._search_compatible_node)
        self.view.requestSearch.connect(self._search_nodes)
        self.library.nodeActivated.connect(self._add_library_node)
        self._autosave_timer.timeout.connect(self._autosave)
        self._activation_timer.timeout.connect(self._activate_graph)
        self._preview_timer.timeout.connect(self._poll_previews)
        self._metrics_timer.timeout.connect(self._refresh_engine_status)
        QApplication.clipboard().dataChanged.connect(self._refresh_action_states)

    @Slot()
    def play(self) -> None:
        target = self._transport_target()
        if target is None:
            return
        try:
            status = self.engine_client.source_status(target)[0]
            if status.state is SourceState.PAUSED:
                self.engine_client.resume(target)
                verb = "Resumed"
            else:
                self.engine_client.play(target)
                verb = "Playing"
        except (KeyError, RuntimeError, TimeoutError, IndexError) as error:
            self.statusBar().showMessage(f"Could not play source: {error}", 5000)
            return
        self.statusBar().showMessage(f"{verb} source {str(target)[:8]}", 3000)

    @Slot()
    def pause(self) -> None:
        self._invoke_transport(self.engine_client.pause, "Paused")

    @Slot()
    def stop(self) -> None:
        self._invoke_transport(self.engine_client.stop, "Stopped")

    @Slot()
    def reload(self) -> None:
        self._invoke_transport(self.engine_client.reload, "Reloaded")

    @Slot()
    def panic(self) -> None:
        try:
            self.engine_client.panic()
        except (RuntimeError, TimeoutError) as error:
            self.statusBar().showMessage(f"Could not send panic: {error}", 5000)
            return
        self.statusBar().showMessage("Panic sent", 3000)

    @Slot()
    def restart_engine(self) -> None:
        if self._engine_closed:
            return
        self.action_registry.require("restart_engine").setEnabled(False)
        self.statusBar().showMessage("Restarting engine…")
        QApplication.processEvents()
        try:
            activation = self.engine_client.restart()
        except (RuntimeError, TimeoutError) as error:
            self.statusBar().showMessage(f"Engine restart failed: {error}", 8000)
            self._refresh_engine_status()
            return
        self._image_sequences.clear()
        self._note_sequences.clear()
        self._engine_failure_signature = None
        if activation is None:
            snapshot = self.session.document.snapshot()
            if snapshot.nodes:
                try:
                    activation = self.engine_client.activate(snapshot)
                except (RuntimeError, TimeoutError) as error:
                    self.statusBar().showMessage(
                        f"Engine restarted but graph rebuild failed: {error}",
                        8000,
                    )
                    self._refresh_engine_status()
                    return
        if activation is not None and not activation.activated:
            self.statusBar().showMessage(
                "Engine restarted; current invalid graph was not activated",
                8000,
            )
        else:
            self.statusBar().showMessage("Engine restarted", 5000)
        self._refresh_engine_status()

    def _invoke_transport(self, operation: Callable[[UUID | None], None], past_tense: str) -> None:
        target = self._transport_target()
        if target is None:
            return
        try:
            operation(target)
        except (KeyError, RuntimeError, TimeoutError) as error:
            self.statusBar().showMessage(f"Could not control source: {error}", 5000)
            return
        self.statusBar().showMessage(f"{past_tense} source {str(target)[:8]}", 3000)

    def _transport_target(self) -> UUID | None:
        sources = tuple(
            node.id
            for node in self.session.document.nodes
            if (definition := self.registry.get(node.type_id)) is not None
            and definition.execution_kind is ExecutionKind.SOURCE
        )
        resolution = resolve_transport_target(sources, self.scene.selected_node_ids())
        if resolution.target is None:
            self.statusBar().showMessage(resolution.message, 4000)
        return resolution.target

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
        missing_count = len(find_missing_media(self.session.document.snapshot()))
        if missing_count:
            self.statusBar().showMessage(
                f"Opened {path.name} · {missing_count} missing media file(s); "
                "use File → Locate Missing Media",
                8000,
            )
        else:
            self.statusBar().showMessage(f"Opened {path.name}", 4000)
        return True

    @Slot()
    def locate_missing_media(self) -> None:
        references = find_missing_media(self.session.document.snapshot())
        selected_node_ids = self.scene.selected_node_ids()
        selected_references = tuple(
            reference for reference in references if reference.node_id in selected_node_ids
        )
        candidates = selected_references or references
        if not candidates:
            self.statusBar().showMessage("No missing media files were found", 4000)
            return
        reference = candidates[0]
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Locate Missing Video",
            str(reference.missing_path.parent),
            "Video files (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*)",
        )
        if not selected:
            return
        verification = verify_relink_candidate(reference, selected)
        if verification.match is RelinkMatch.MISMATCH:
            choice = QMessageBox.warning(
                self,
                "Media identity differs",
                f"{verification.message}\n\nUse this explicitly selected file anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice is not QMessageBox.StandardButton.Yes:
                return
        try:
            self.session.relink_media(reference.node_id, selected)
        except (OSError, ValueError) as error:
            self._show_error("Could not relink media", str(error))
            return
        self.scene.select_node_ids({reference.node_id})
        remaining = len(find_missing_media(self.session.document.snapshot()))
        self.statusBar().showMessage(
            f"Relinked {Path(selected).name} · {remaining} missing media file(s) remain",
            5000,
        )

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
    def add_group_at_center(self) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        group_id = self.session.add_group(
            GroupKind.GROUP,
            (center.x() - 240.0, center.y() - 160.0),
            title="Group",
        )
        self.scene.select_group_ids({group_id})

    @Slot()
    def add_comment_at_center(self) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        group_id = self.session.add_group(
            GroupKind.COMMENT,
            (center.x() - 160.0, center.y() - 80.0),
            title="Comment",
            text="Double-click to edit this comment.",
            size=(320.0, 160.0),
            color="#5a4f36",
        )
        self.scene.select_group_ids({group_id})

    @Slot()
    def _refresh_document_ui(self) -> None:
        path = self.session.current_path
        name = path.name if path is not None else "Untitled"
        self.setWindowTitle(f"{name}[*] — Synesthesia Machine {__version__}")
        self.setWindowModified(self.session.is_dirty)
        snapshot = self.session.document.snapshot()
        self._node_count.setText(
            f"{len(snapshot.nodes)} node(s) · {len(snapshot.connections)} cable(s) · "
            f"{len(snapshot.groups)} group/comment(s)"
        )
        errors = len(self.session.report.errors)
        warnings = len(self.session.report.warnings)
        self._validation_status.setText(f"{errors} error(s) · {warnings} warning(s)")
        self._validation_status.setToolTip(
            "\n".join(
                f"{issue.severity}: {issue.message} ({issue.code})"
                for issue in self.session.report.issues[:8]
            )
            or "Graph is valid"
        )
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
        has_selection = (
            has_nodes
            or bool(self.scene.selected_connection_ids())
            or bool(self.scene.selected_group_ids())
        )
        self.action_registry.require("copy").setEnabled(has_nodes)
        self.action_registry.require("duplicate").setEnabled(has_nodes)
        self.action_registry.require("delete").setEnabled(has_selection)
        mime_data = _clipboard_mime_data()
        self.action_registry.require("paste").setEnabled(
            mime_data is not None and mime_data.hasFormat(CLIPBOARD_MIME_TYPE)
        )
        self.action_registry.require("frame_selection").setEnabled(has_selection)
        selected_node_count = len(self.scene.selected_node_ids())
        for key in (
            "align_left",
            "align_hcenter",
            "align_right",
            "align_top",
            "align_vcenter",
            "align_bottom",
            "tidy_selection",
        ):
            self.action_registry.require(key).setEnabled(selected_node_count >= 2)
        self.action_registry.require("distribute_horizontal").setEnabled(selected_node_count >= 3)
        self.action_registry.require("distribute_vertical").setEnabled(selected_node_count >= 3)
        sources = tuple(
            node.id
            for node in self.session.document.nodes
            if (definition := self.registry.get(node.type_id)) is not None
            and definition.execution_kind is ExecutionKind.SOURCE
        )
        transport = resolve_transport_target(sources, self.scene.selected_node_ids())
        for key, verb in (
            ("play", "Play or resume"),
            ("pause", "Pause"),
            ("stop", "Stop"),
            ("reload", "Reload"),
        ):
            action = self.action_registry.require(key)
            action.setEnabled(transport.target is not None)
            action.setToolTip(
                f"{verb} {transport.message.removeprefix('Targeting ').lower()}"
                if transport.target is not None
                else transport.message
            )

    @Slot(object)
    def _focus_validation_issue(self, issue: ValidationIssue) -> None:
        if issue.node_id is not None:
            self.scene.select_node_ids({issue.node_id})
            item = self.scene.node_items.get(issue.node_id)
            if item is not None:
                self.view.ensureVisible(item, 80, 80)
        elif issue.connection_id is not None:
            self.scene.select_connection_ids({issue.connection_id})
            item = self.scene.connection_items.get(issue.connection_id)
            if item is not None:
                self.view.ensureVisible(item, 80, 80)
        self.statusBar().showMessage(f"{issue.code}: {issue.message}", 6000)

    @Slot()
    def _schedule_engine_activation(self) -> None:
        if not self._engine_closed:
            self._activation_timer.start()

    @Slot()
    def _activate_graph(self) -> None:
        if self._engine_closed:
            return
        snapshot = self.session.document.snapshot()
        try:
            activation = self.engine_client.activate(snapshot)
        except (RuntimeError, TimeoutError) as error:
            self.statusBar().showMessage(f"Engine activation failed: {error}", 5000)
            return
        if activation.activated:
            self._image_sequences.clear()
            self._note_sequences.clear()
            self.statusBar().showMessage(
                f"Activated graph revision {activation.graph_revision}", 3000
            )
            return
        count = len(activation.report.errors)
        self.statusBar().showMessage(
            f"Graph has {count} error(s); previous valid runtime remains active",
            5000,
        )

    @Slot()
    def _poll_previews(self) -> None:
        if self._engine_closed or not self.preview_dock.isVisible():
            return
        try:
            image_previews = self.engine_client.poll_image_previews(self._image_sequences)
            note_previews = self.engine_client.poll_note_previews(self._note_sequences)
        except (RuntimeError, TimeoutError):
            return
        for preview in image_previews:
            self._image_sequences[preview.node_id] = preview.sequence
            self.preview_panel.show_image_preview(preview)
        for preview in note_previews:
            self._note_sequences[preview.node_id] = preview.sequence
            self.preview_panel.show_note_preview(preview)

    @Slot()
    def _refresh_engine_status(self) -> None:
        if self._engine_closed:
            return
        try:
            engine_status = self.engine_client.status()
        except (RuntimeError, TimeoutError):
            return
        restart = self.action_registry.require("restart_engine")
        failed = engine_status.connection_state in {
            EngineConnectionState.CRASHED,
            EngineConnectionState.UNRESPONSIVE,
        }
        restart.setEnabled(failed)
        if failed:
            self._show_engine_failure(engine_status)
            return
        self._engine_failure_signature = None
        if engine_status.connection_state is EngineConnectionState.RESTARTING:
            self._engine_status.setText("Engine RESTARTING")
            return
        try:
            metrics = self.engine_client.metrics()
            sources = self.engine_client.source_status()
            selected_nodes = self.scene.selected_node_ids()
            diagnostic = None
            if len(selected_nodes) == 1:
                diagnostics = self.engine_client.node_memory_diagnostics(next(iter(selected_nodes)))
                diagnostic = diagnostics[0] if diagnostics else None
        except (RuntimeError, TimeoutError):
            return
        self.inspector.set_memory_diagnostic(diagnostic)
        source_text = ", ".join(status.state.value for status in sources) or "no source"
        memory_mib = metrics.memory_bytes / (1024 * 1024)
        self._engine_status.setText(
            f"Engine {metrics.state.value} · source {source_text} · "
            f"{metrics.processed_ticks} ticks @ {metrics.processed_fps:.1f} FPS · "
            f"p95 {metrics.p95_node_time_ms:.2f} ms · "
            f"drops {metrics.dropped_before_processing} · RAM {memory_mib:.1f} MiB"
        )

    def _show_engine_failure(self, status: EngineStatus) -> None:
        state = status.connection_state.value
        exit_text = "" if status.exit_code is None else f" · exit {status.exit_code}"
        log_text = (
            "" if status.crash_log_path is None else f" · crash log path {status.crash_log_path}"
        )
        self._engine_status.setText(f"Engine {state} · STOPPED{exit_text}{log_text}")
        detail = status.last_error or "The engine process stopped unexpectedly."
        self.statusBar().showMessage(f"Engine {state.lower()}: {detail}")
        signature = (
            status.connection_state,
            status.child_process_id,
            status.exit_code,
            status.last_error,
            status.crash_log_path,
        )
        if signature == self._engine_failure_signature:
            return
        self._engine_failure_signature = signature
        message = (
            f"The engine is {state.lower()}. The graph document remains open and editable.\n\n"
            f"Details: {detail}"
        )
        if status.exit_code is not None:
            message += f"\nExit code: {status.exit_code}"
        if status.crash_log_path is not None:
            message += f"\nCrash-log path: {status.crash_log_path}"
            message += "\nA forced termination may not produce a Python traceback file."
        message += "\n\nUse Graph → Restart Engine to rebuild the latest valid runtime."
        QMessageBox.critical(self, "Engine stopped", message)

    @Slot()
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Synesthesia Machine",
            f"Synesthesia Machine {__version__}\n\n"
            "Video and image processing mapped to visualized MIDI state.",
        )

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
            path = self.autosave_store.save(
                self.session.document.snapshot(),
                explicit_path=self.session.current_path,
            )
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
        if record.explicit_path is not None:
            return record.explicit_path
        for path in self._recent_paths:
            try:
                snapshot = load_graph(path, self.registry)
            except (GraphPersistenceError, OSError, ValueError):
                continue
            if snapshot.document_id == record.document_id:
                return path
        return None

    def _load_recent_paths(self) -> list[Path]:
        return self.settings_store.load_recent_files(self.preferences.recent_file_limit)

    def _remember_recent(self, path: Path) -> None:
        self._recent_paths = self.settings_store.remember_recent(
            path,
            self._recent_paths,
            self.preferences.recent_file_limit,
        )
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
        self.recent_menu.addSeparator()
        clear_action = QAction("&Clear Recent Graphs", self.recent_menu)
        clear_action.triggered.connect(self._clear_recent)
        self.recent_menu.addAction(clear_action)

    @Slot()
    def _clear_recent(self) -> None:
        self._recent_paths = []
        self.settings_store.clear_recent()
        self._refresh_recent_menu()

    @Slot()
    def edit_preferences(self) -> None:
        dialog = PreferencesDialog(self.preferences, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_preferences(dialog.preferences())

    def apply_preferences(self, preferences: EditorPreferences) -> None:
        self.preferences = preferences
        self.settings_store.save_preferences(preferences)
        self._autosave_timer.setInterval(preferences.autosave_delay_seconds * 1000)
        self.scene.configure_grid_snap(
            enabled=preferences.grid_snap_enabled,
            spacing=preferences.grid_size,
        )
        self._recent_paths = self._recent_paths[: preferences.recent_file_limit]
        self.settings.setValue("recentFiles", [str(path) for path in self._recent_paths])
        self._refresh_recent_menu()

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
            self._activation_timer.stop()
            self._preview_timer.stop()
            self._metrics_timer.stop()
            if not self._engine_closed:
                self.engine_client.close()
                self._engine_closed = True
            self._save_window_state()
            event.accept()
            return
        event.ignore()
