"""Graph editor, EngineClient transport controls, and runtime preview orchestration."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QObject,
    QPointF,
    QSettings,
    Qt,
    QTimer,
    Signal,
    Slot,
)
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
    QToolButton,
)

from synesthesia_machine import __version__
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineClient,
    EngineConnectionState,
    EngineMetrics,
    EngineStatus,
    MidiOutputConnectionState,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.diagnostics import create_diagnostic_bundle
from synesthesia_machine.graph import (
    AlignMode,
    DistributionAxis,
    GraphSnapshot,
    GroupKind,
    ValidationIssue,
    generate_random_graph,
)
from synesthesia_machine.nodes import ExecutionKind, NodeRegistry
from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
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
from synesthesia_machine.ui.autosave_controller import AutosaveController
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.commands import PREVIEW_VISIBLE_KEY
from synesthesia_machine.ui.previews import (
    ImagePreviewPanel,
    NotePreviewPanel,
    image_preview_to_qimage,
)
from synesthesia_machine.ui.profiler import ProfilerPanel, profile_heat_levels
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME, Theme
from synesthesia_machine.ui.translations import set_language, tr, trf
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


@dataclass(frozen=True, slots=True)
class _EngineRefreshSnapshot:
    metrics: EngineMetrics
    sources: tuple[SourceStatus, ...]
    midi_outputs: tuple[MidiOutputStatus, ...]
    diagnostic: NodeMemoryDiagnostic | None


class _EngineTaskSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, object)


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
        set_language(self.preferences.language)
        self.session = DocumentSession(registry, self)
        self.autosave_store = AutosaveStore(paths.recovery)
        self.autosave_controller = AutosaveController(self.autosave_store, self)
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
        self.image_preview_panel = ImagePreviewPanel(self)
        self.note_preview_panel = NotePreviewPanel(self)
        self.profiler_panel = ProfilerPanel(self)
        self.recent_menu = QMenu(tr("Open &Recent"), self)
        self._recent_paths = self._load_recent_paths()
        self._image_sequences: dict[tuple[UUID, str], int] = {}
        self._note_sequences: dict[UUID, int] = {}
        self._canvas_value_sequences: dict[tuple[UUID, str], int] = {}
        self._image_dock_preview_sources: frozenset[tuple[UUID, str]] = frozenset()
        self._engine_closed = False
        self._engine_failure_signature: tuple[object, ...] | None = None
        self._source_error_signature: tuple[tuple[UUID, str], ...] = ()
        self._midi_error_signature: tuple[tuple[UUID, str, tuple[str, ...]], ...] = ()
        self._runtime_error_signature: tuple[tuple[UUID, str, str], ...] = ()
        self._engine_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="synmachine-ui-engine",
        )
        self._engine_task_signals = _EngineTaskSignals(self)
        self._engine_tasks_inflight: set[str] = set()
        self._pending_activation: tuple[GraphSnapshot, tuple[UUID, ...]] | None = None
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
        self._profiler_timer = QTimer(self)
        self._profiler_timer.setInterval(250)
        self._device_refresh_timer = QTimer(self)
        self._device_refresh_timer.setSingleShot(True)
        self._device_refresh_timer.setInterval(1500)
        self._device_refresh_timer.timeout.connect(self._load_device_catalogue)
        self._node_count = QLabel(self)
        self._validation_status = QLabel(self)
        self._engine_status = QLabel(self)

        self.setObjectName("main_window")
        self.setAccessibleName(tr("Synesthesia Machine graph editor"))
        self.setWindowTitle(f"{tr('Untitled')}[*] — Synesthesia Machine")
        self.setWindowModified(False)
        self.resize(1280, 760)
        self.setMinimumSize(900, 560)
        application = cast(QApplication | None, QApplication.instance())
        if application is not None:
            application.setStyleSheet(theme.style_sheet())
        self.setStyleSheet(theme.style_sheet())
        self.setCentralWidget(self.view)
        self._create_docks()
        self._create_actions()
        self._create_toolbar()
        self._create_menus()
        self._create_status_bar()
        self._connect_signals()
        self._restore_window_state()
        # Profiling is opt-in every launch even when an older saved layout left the dock visible.
        self.profiler_dock.hide()
        self._refresh_recent_menu()
        self._refresh_document_ui()
        self._refresh_selection_ui()
        self._preview_timer.start()
        self._metrics_timer.start()
        # Let initial engine status/activation settle before the first hardware catalogue request.
        # The owned timer is cancelled with the window; users can refresh immediately from the menu.
        self._device_refresh_timer.start()
        if offer_recovery:
            QTimer.singleShot(0, self._offer_recovery)

    def _create_docks(self) -> None:
        self.library_dock = QDockWidget(tr("Node Library"), self)
        self.library_dock.setObjectName("node_library_dock")
        self.library_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.library_dock.setWidget(self.library)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)

        self.inspector_dock = QDockWidget(tr("Inspector"), self)
        self.inspector_dock.setObjectName("inspector_dock")
        self.inspector_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.inspector_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock)

        preview_areas = (
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.image_preview_dock = QDockWidget(tr("Image Preview"), self)
        self.image_preview_dock.setObjectName("image_preview_dock")
        self.image_preview_dock.setAllowedAreas(preview_areas)
        self.image_preview_dock.setWidget(self.image_preview_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.image_preview_dock)

        self.note_preview_dock = QDockWidget(tr("Note Visualizer"), self)
        self.note_preview_dock.setObjectName("note_preview_dock")
        self.note_preview_dock.setAllowedAreas(preview_areas)
        self.note_preview_dock.setWidget(self.note_preview_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.note_preview_dock)
        self.tabifyDockWidget(self.image_preview_dock, self.note_preview_dock)
        self.image_preview_dock.raise_()

        self.profiler_dock = QDockWidget(tr("Runtime Profiler"), self)
        self.profiler_dock.setObjectName("runtime_profiler_dock")
        self.profiler_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.profiler_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.profiler_dock.setWidget(self.profiler_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.profiler_dock)
        self.profiler_dock.hide()

        self.issues_dock = QDockWidget(tr("Validation Issues"), self)
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

        undo = self.session.undo_stack.createUndoAction(self, tr("&Undo"))
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.action_registry.register(
            "undo", undo, status_tip="Undo the last graph edit", source_text="&Undo"
        )
        redo = self.session.undo_stack.createRedoAction(self, tr("&Redo"))
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.action_registry.register(
            "redo", redo, status_tip="Redo the last graph edit", source_text="&Redo"
        )
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
            ActionSpec(
                "randomize_parameters",
                "Randomize &Parameters",
                "Randomize parameters on the selected nodes",
                "Ctrl+Shift+P",
            ),
            self.randomize_parameters,
        )
        create(
            ActionSpec(
                "randomize_nodes",
                "Randomize &Nodes",
                "Replace selected nodes with a valid randomized subgraph",
                "Ctrl+Shift+R",
            ),
            self.randomize_nodes,
        )
        create(
            ActionSpec(
                "organize_graph",
                "&Organize Graph",
                "Arrange every node and visualizer into non-overlapping graph layers",
            ),
            self.organize_graph,
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
            ActionSpec(
                "panic",
                "&Silence All Outputs",
                "Immediately stop all MIDI notes and generated audio",
                "Ctrl+Shift+Escape",
            ),
            self.panic,
        )
        restart_engine = create(
            ActionSpec(
                "restart_engine",
                "Restart &Engine",
                "Restart the child engine and rebuild the latest valid runtime",
                "Ctrl+Alt+R",
            ),
            self.restart_engine,
        )
        restart_engine.setEnabled(False)
        create(
            ActionSpec(
                "refresh_devices",
                "Refresh &Devices",
                "Refresh camera, MIDI, and audio output choices in the engine",
            ),
            self.refresh_devices,
        )
        create(
            ActionSpec(
                "export_diagnostics",
                "Export &Diagnostic Bundle…",
                "Export redacted hardware, runtime, graph, dependency, and log diagnostics",
            ),
            self.export_diagnostic_bundle,
        )
        create(ActionSpec("about", "&About", "About Synesthesia Machine"), self._show_about)
        self.action_registry.register(
            "toggle_library",
            self.library_dock.toggleViewAction(),
            status_tip="Show or hide the node library",
            source_text="Node Library",
        )
        self.action_registry.register(
            "toggle_inspector",
            self.inspector_dock.toggleViewAction(),
            status_tip="Show or hide the inspector",
            source_text="Inspector",
        )
        self.action_registry.register(
            "toggle_image_preview",
            self.image_preview_dock.toggleViewAction(),
            status_tip="Show or hide the image preview",
            source_text="Image Preview",
        )
        self.action_registry.register(
            "toggle_note_preview",
            self.note_preview_dock.toggleViewAction(),
            status_tip="Show or hide the note velocity visualizer",
            source_text="Note Visualizer",
        )
        self.action_registry.register(
            "toggle_profiler",
            self.profiler_dock.toggleViewAction(),
            status_tip="Show or hide per-node runtime profiling",
            source_text="Runtime Profiler",
        )
        self.action_registry.register(
            "toggle_issues",
            self.issues_dock.toggleViewAction(),
            status_tip="Show or hide the graph validation issue list",
            source_text="Validation Issues",
        )

    def _create_toolbar(self) -> None:
        toolbar = QToolBar(tr("Transport"), self)
        toolbar.setObjectName("transport_toolbar")
        toolbar.setAccessibleName(tr("Source transport and output silence"))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for key in ("play", "pause", "stop", "reload"):
            action = self.action_registry.require(key)
            toolbar.addAction(action)
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setObjectName(f"transport_{key}_button")
                button.setProperty("transportControl", True)
                button.setAccessibleName(action.text().replace("&", ""))
        toolbar.addSeparator()
        toolbar.addAction(self.action_registry.require("panic"))
        toolbar.addSeparator()
        for key in ("randomize_parameters", "randomize_nodes", "organize_graph"):
            random_action = self.action_registry.require(key)
            toolbar.addAction(random_action)
            random_button = toolbar.widgetForAction(random_action)
            if isinstance(random_button, QToolButton):
                random_button.setObjectName(f"{key}_button")
                random_button.setProperty("transportControl", True)
                random_button.setAccessibleName(random_action.text().replace("&", ""))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.transport_toolbar = toolbar

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu(tr("&File"))
        file_menu.addAction(self.action_registry.require("new"))
        file_menu.addAction(self.action_registry.require("open"))
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(self.action_registry.require("relink_media"))
        file_menu.addSeparator()
        file_menu.addAction(self.action_registry.require("save"))
        file_menu.addAction(self.action_registry.require("save_as"))
        file_menu.addSeparator()
        file_menu.addAction(self.action_registry.require("exit"))

        edit_menu = self.menuBar().addMenu(tr("&Edit"))
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

        view_menu = self.menuBar().addMenu(tr("&View"))
        view_menu.addAction(self.action_registry.require("frame_selection"))
        view_menu.addAction(self.action_registry.require("frame_all"))
        view_menu.addSeparator()
        view_menu.addAction(self.action_registry.require("toggle_library"))
        view_menu.addAction(self.action_registry.require("toggle_inspector"))
        view_menu.addAction(self.action_registry.require("toggle_image_preview"))
        view_menu.addAction(self.action_registry.require("toggle_note_preview"))
        view_menu.addAction(self.action_registry.require("toggle_profiler"))
        view_menu.addAction(self.action_registry.require("toggle_issues"))

        graph_menu = self.menuBar().addMenu(tr("&Graph"))
        graph_menu.addAction(self.action_registry.require("add_node"))
        graph_menu.addAction(self.action_registry.require("add_group"))
        graph_menu.addAction(self.action_registry.require("add_comment"))
        graph_menu.addAction(self.action_registry.require("randomize_parameters"))
        graph_menu.addAction(self.action_registry.require("randomize_nodes"))
        graph_menu.addAction(self.action_registry.require("organize_graph"))
        arrange_menu = graph_menu.addMenu(tr("&Arrange Selection"))
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
        graph_menu.addAction(self.action_registry.require("refresh_devices"))

        output_menu = self.menuBar().addMenu(tr("&Outputs"))
        output_menu.addAction(self.action_registry.require("panic"))

        help_menu = self.menuBar().addMenu(tr("&Help"))
        help_menu.addAction(self.action_registry.require("export_diagnostics"))
        help_menu.addSeparator()
        help_menu.addAction(self.action_registry.require("about"))
        self._menus = {
            "file": file_menu,
            "edit": edit_menu,
            "view": view_menu,
            "graph": graph_menu,
            "arrange": arrange_menu,
            "outputs": output_menu,
            "help": help_menu,
        }

    def _create_status_bar(self) -> None:
        self.statusBar().showMessage(tr("Ready"))
        self._engine_status.setText(tr("Engine STOPPED · 0 ticks"))
        self._engine_status.setAccessibleName(tr("Engine runtime status"))
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
        self.scene.connectionInspectRequested.connect(self._inspect_connection)
        self.view.requestSearch.connect(self._search_nodes)
        self.library.nodeActivated.connect(self._add_library_node)
        self._autosave_timer.timeout.connect(self._autosave)
        self._activation_timer.timeout.connect(self._activate_graph)
        self._preview_timer.timeout.connect(self._poll_previews)
        self.image_preview_dock.visibilityChanged.connect(self._on_image_preview_visibility_changed)
        self.note_preview_dock.visibilityChanged.connect(self._on_note_preview_visibility_changed)
        self._metrics_timer.timeout.connect(self._refresh_engine_status)
        self._profiler_timer.timeout.connect(self._refresh_profiles)
        self.profiler_dock.visibilityChanged.connect(self._on_profiler_visibility_changed)
        self.profiler_panel.resetRequested.connect(self._reset_profiling)
        self.profiler_panel.heatmapChanged.connect(self._update_profiler_heatmap)
        QApplication.clipboard().dataChanged.connect(self._refresh_action_states)
        self._engine_task_signals.completed.connect(self._on_engine_task_completed)
        self._engine_task_signals.failed.connect(self._on_engine_task_failed)
        self.autosave_controller.saved.connect(self._on_autosave_saved)
        self.autosave_controller.failed.connect(self._on_autosave_failed)

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
            self.statusBar().showMessage(trf("Could not play source: {error}", error=error), 5000)
            return
        self.statusBar().showMessage(
            trf("{verb} source {source}", verb=tr(verb), source=str(target)[:8]), 3000
        )

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
            self.statusBar().showMessage(trf("Could not send panic: {error}", error=error), 5000)
            return
        self.statusBar().showMessage(tr("All outputs silenced"), 3000)

    @Slot()
    def restart_engine(self) -> None:
        if self._engine_closed:
            return
        self.action_registry.require("restart_engine").setEnabled(False)
        self.statusBar().showMessage(tr("Restarting engine…"))
        QApplication.processEvents()
        try:
            activation = self.engine_client.restart()
        except (RuntimeError, TimeoutError) as error:
            self.statusBar().showMessage(trf("Engine restart failed: {error}", error=error), 8000)
            self._refresh_engine_status()
            return
        self._clear_runtime_previews()
        self._engine_failure_signature = None
        if activation is None:
            snapshot = self.session.document.snapshot()
            if snapshot.nodes:
                try:
                    activation = self.engine_client.activate(snapshot)
                except (RuntimeError, TimeoutError) as error:
                    self.statusBar().showMessage(
                        trf("Engine restarted but graph rebuild failed: {error}", error=error),
                        8000,
                    )
                    self._refresh_engine_status()
                    return
        if activation is not None and not activation.activated:
            self.statusBar().showMessage(
                tr("Engine restarted; current invalid graph was not activated"),
                8000,
            )
        else:
            self.statusBar().showMessage(tr("Engine restarted"), 5000)
        self._refresh_engine_status()

    def _invoke_transport(self, operation: Callable[[UUID | None], None], past_tense: str) -> None:
        target = self._transport_target()
        if target is None:
            return
        try:
            operation(target)
        except (KeyError, RuntimeError, TimeoutError) as error:
            self.statusBar().showMessage(
                trf("Could not control source: {error}", error=error), 5000
            )
            return
        self.statusBar().showMessage(
            trf(
                "{verb} source {source}",
                verb=tr(past_tense),
                source=str(target)[:8],
            ),
            3000,
        )

    def _transport_target(self) -> UUID | None:
        sources = tuple(
            node.id
            for node in self.session.document.nodes
            if (definition := self.registry.get(node.type_id)) is not None
            and definition.execution_kind is ExecutionKind.SOURCE
        )
        resolution = resolve_transport_target(sources, self.scene.selected_node_ids())
        if resolution.target is None:
            self.statusBar().showMessage(tr(resolution.message), 4000)
        return resolution.target

    @Slot()
    def new_document(self) -> None:
        previous_id = self.session.document.document_id
        decision = self._confirm_document_replacement()
        if decision is _ReplacementDecision.CANCEL:
            return
        self.session.new_document()
        if decision is _ReplacementDecision.DISCARD:
            self._discard_recovery(previous_id)
        self.statusBar().showMessage(tr("Created new graph"), 3000)

    @Slot()
    def randomize_parameters(self) -> None:
        selected = self.scene.selected_node_ids()
        if not selected:
            self.statusBar().showMessage(
                tr("Select one or more nodes to randomize their parameters"),
                4000,
            )
            return
        try:
            node_count, parameter_count = self.session.randomize_parameters(selected)
        except (KeyError, RuntimeError, ValueError) as error:
            self._show_error("Could not randomize parameters", str(error))
            return
        if not node_count:
            self.statusBar().showMessage(
                tr("The selected nodes have no parameters that can be randomized"),
                4000,
            )
            return
        self.scene.select_node_ids(selected)
        self.statusBar().showMessage(
            trf(
                "Randomized {parameters} parameter(s) across {nodes} selected node(s)",
                parameters=parameter_count,
                nodes=node_count,
            ),
            4000,
        )

    @Slot()
    def randomize_nodes(self) -> None:
        selected = self.scene.selected_node_ids()
        if not selected:
            previous_id = self.session.document.document_id
            decision = self._confirm_document_replacement()
            if decision is _ReplacementDecision.CANCEL:
                return
            try:
                snapshot = generate_random_graph(self.registry)
            except (KeyError, RuntimeError, ValueError) as error:
                self._show_error("Could not generate random graph", str(error))
                return
            self.session.replace_with_snapshot(snapshot)
            if decision is _ReplacementDecision.DISCARD:
                self._discard_recovery(previous_id)
            self.scene.select_node_ids(set())
            QTimer.singleShot(0, self.view.frame_all)
            self.statusBar().showMessage(
                trf(
                    "Generated a complete randomized graph with {nodes} nodes",
                    nodes=len(snapshot.nodes),
                ),
                4000,
            )
            return
        try:
            replacement_ids = self.session.randomize_nodes(selected)
        except (KeyError, RuntimeError, ValueError) as error:
            self._show_error("Could not randomize nodes", str(error))
            return
        self.scene.select_node_ids(set(replacement_ids))
        self.statusBar().showMessage(
            trf(
                "Replaced {selected} selected node(s) with {replacements} randomized node(s)",
                selected=len(selected),
                replacements=len(replacement_ids),
            ),
            4000,
        )

    @Slot()
    def organize_graph(self) -> None:
        if len(self.session.document.nodes) < 2:
            return
        self.scene.organize_graph()
        QTimer.singleShot(0, self.view.frame_all)
        self.statusBar().showMessage(tr("Organized the complete graph"), 4000)

    @Slot()
    def open_document(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, tr("Open Graph"), "", tr(GRAPH_FILE_FILTER))
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
            self._discard_recovery(previous_id)
        self._remember_recent(path)
        missing_count = len(find_missing_media(self.session.document.snapshot()))
        if missing_count:
            self.statusBar().showMessage(
                trf(
                    "Opened {name} · {count} missing media file(s); "
                    "use File → Locate Missing Media",
                    name=path.name,
                    count=missing_count,
                ),
                8000,
            )
        else:
            self.statusBar().showMessage(trf("Opened {name}", name=path.name), 4000)
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
            self.statusBar().showMessage(tr("No missing media files were found"), 4000)
            return
        reference = candidates[0]
        selected, _ = QFileDialog.getOpenFileName(
            self,
            tr("Locate Missing Video"),
            str(reference.missing_path.parent),
            tr("Video files (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*)"),
        )
        if not selected:
            return
        verification = verify_relink_candidate(reference, selected)
        if verification.match is RelinkMatch.MISMATCH:
            choice = QMessageBox.warning(
                self,
                tr("Media identity differs"),
                trf(
                    "{message}\n\nUse this explicitly selected file anyway?",
                    message=verification.message,
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        try:
            self.session.relink_media(reference.node_id, selected)
        except (OSError, ValueError) as error:
            self._show_error("Could not relink media", str(error))
            return
        self.scene.select_node_ids({reference.node_id})
        remaining = len(find_missing_media(self.session.document.snapshot()))
        self.statusBar().showMessage(
            trf(
                "Relinked {name} · {count} missing media file(s) remain",
                name=Path(selected).name,
                count=remaining,
            ),
            5000,
        )

    @Slot()
    def save_document(self) -> bool:
        if self.session.current_path is None:
            return self.save_document_as()
        return self._save_to_path(self.session.current_path)

    @Slot()
    def save_document_as(self) -> bool:
        initial = str(
            self.session.current_path or Path.home() / f"{tr('Untitled')}.synmachine.json"
        )
        selected, _ = QFileDialog.getSaveFileName(
            self, tr("Save Graph"), initial, tr(GRAPH_FILE_FILTER)
        )
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
        self._discard_recovery(document_id)
        self._remember_recent(saved)
        self.statusBar().showMessage(trf("Saved {name}", name=saved.name), 4000)
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
        self.statusBar().showMessage(trf("Copied {count} node(s)", count=len(node_ids)), 2500)

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
        self.statusBar().showMessage(trf("Pasted {count} node(s)", count=len(node_ids)), 2500)

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
        dialog = NodeSearchDialog(candidates, self, title=tr("Add Node"))
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
            self.statusBar().showMessage(tr("No compatible node types are registered"), 3500)
            return
        dialog = NodeSearchDialog(candidates, self, title=tr("Add Compatible Node"))
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

    @Slot(object)
    def _inspect_connection(self, value: object) -> None:
        if not isinstance(value, UUID):
            return
        connection = next(
            (
                connection
                for connection in self.session.view_model.connections
                if connection.connection_id == value
            ),
            None,
        )
        model = self.session.document.connection(value)
        if connection is None or model is None:
            return
        visualizers = {
            "IMAGE": (DISPLAY_IMAGE_DATA_TYPE_ID, "image", self.image_preview_dock),
            "CHANNEL": (CHANNEL_DISPLAY_TYPE_ID, "channel", self.image_preview_dock),
            "MIDI_STATE": (NOTE_VISUALIZER_TYPE_ID, "midi", self.note_preview_dock),
        }
        target = visualizers.get(connection.type_name)
        if target is not None:
            type_id, input_port, dock = target
            destination = self.session.document.node(model.destination_node_id)
            position = destination.position if destination is not None else (0.0, 0.0)
            self.session.undo_stack.beginMacro(tr("Inspect connection"))
            try:
                inspector_id = self.session.add_node(
                    type_id,
                    (position[0], position[1] + 260.0),
                )
                self.session.add_connection(
                    model.source_node_id,
                    model.source_port_id,
                    inspector_id,
                    input_port,
                )
            finally:
                self.session.undo_stack.endMacro()
            self.scene.select_node_ids({inspector_id})
            dock.show()
            dock.raise_()
            self.statusBar().showMessage(
                trf(
                    "Attached a live {type_name} inspector",
                    type_name=connection.type_name.lower(),
                ),
                4000,
            )
            return
        self.profiler_dock.show()
        self.profiler_dock.raise_()
        self.scene.select_node_ids({model.source_node_id})
        self.statusBar().showMessage(
            tr("Live value is shown in the source node's Output column"), 5000
        )

    @Slot()
    def add_group_at_center(self) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        group_id = self.session.add_group(
            GroupKind.GROUP,
            (center.x() - 240.0, center.y() - 160.0),
            title=tr("Group"),
        )
        self.scene.select_group_ids({group_id})

    @Slot()
    def add_comment_at_center(self) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        group_id = self.session.add_group(
            GroupKind.COMMENT,
            (center.x() - 160.0, center.y() - 80.0),
            title=tr("Comment"),
            text=tr("Double-click to edit this comment."),
            size=(320.0, 160.0),
            color="#5a4f36",
        )
        self.scene.select_group_ids({group_id})

    @Slot()
    def _refresh_document_ui(self) -> None:
        path = self.session.current_path
        name = path.name if path is not None else tr("Untitled")
        self.setWindowTitle(f"{name}[*] — Synesthesia Machine {__version__}")
        self.setWindowModified(self.session.is_dirty)
        snapshot = self.session.document.snapshot()
        self._image_dock_preview_sources = self._image_visualizer_source_keys(snapshot)
        self._node_count.setText(
            trf(
                "{nodes} node(s) · {cables} cable(s) · {groups} group/comment(s)",
                nodes=len(snapshot.nodes),
                cables=len(snapshot.connections),
                groups=len(snapshot.groups),
            )
        )
        errors = len(self.session.report.errors)
        warnings = len(self.session.report.warnings)
        self._validation_status.setText(
            trf("{errors} error(s) · {warnings} warning(s)", errors=errors, warnings=warnings)
        )
        self._validation_status.setToolTip(
            "\n".join(
                f"{tr(str(issue.severity))}: {tr(issue.message)} ({issue.code})"
                for issue in self.session.report.issues[:8]
            )
            or tr("Graph is valid")
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
        self.action_registry.require("organize_graph").setEnabled(
            len(self.session.document.nodes) >= 2
        )
        sources = tuple(
            node.id
            for node in self.session.document.nodes
            if (definition := self.registry.get(node.type_id)) is not None
            and definition.execution_kind is ExecutionKind.SOURCE
        )
        transport = resolve_transport_target(sources, self.scene.selected_node_ids())
        selected = self.scene.selected_node_ids()
        for key, verb in (
            ("play", "Play or resume"),
            ("pause", "Pause"),
            ("stop", "Stop"),
            ("reload", "Reload"),
        ):
            action = self.action_registry.require(key)
            action.setEnabled(transport.target is not None)
            action.setToolTip(
                trf(
                    "{verb} the {target} source",
                    verb=tr(verb),
                    target=tr("selected" if transport.target in selected else "only"),
                )
                if transport.target is not None
                else tr(transport.message)
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
        self.statusBar().showMessage(f"{issue.code}: {tr(issue.message)}", 6000)

    @Slot()
    def _schedule_engine_activation(self) -> None:
        if not self._engine_closed:
            self._activation_timer.start()

    def _submit_engine_task(self, kind: str, operation: Callable[[], object]) -> bool:
        if self._engine_closed or kind in self._engine_tasks_inflight:
            return False
        self._engine_tasks_inflight.add(kind)
        future = self._engine_executor.submit(operation)
        future.add_done_callback(partial(self._publish_engine_task_result, kind))
        return True

    def _publish_engine_task_result(self, kind: str, future: Future[object]) -> None:
        try:
            result = future.result()
        except BaseException as error:
            with suppress(RuntimeError):
                self._engine_task_signals.failed.emit(kind, error)
            return
        with suppress(RuntimeError):
            self._engine_task_signals.completed.emit(kind, result)

    @Slot(str, object)
    def _on_engine_task_completed(self, kind: str, result: object) -> None:
        self._engine_tasks_inflight.discard(kind)
        if self._engine_closed:
            return
        if kind == "activation":
            if not isinstance(result, EngineActivation):
                raise TypeError("Engine activation task returned an invalid result")
            self._apply_engine_activation(result)
            self._start_pending_activation()
        elif kind == "refresh":
            if not isinstance(result, _EngineRefreshSnapshot):
                raise TypeError("Engine refresh task returned an invalid result")
            self._apply_engine_refresh(result)
        elif kind == "devices":
            if not isinstance(result, DeviceCatalogue):
                raise TypeError("Device catalogue task returned an invalid result")
            self.session.set_device_catalogue(result)
            if result.pending_kinds:
                self._device_refresh_timer.start(250)
            if result.errors:
                detail = "; ".join(f"{kind.value}: {message}" for kind, message in result.errors)
                self.statusBar().showMessage(
                    trf("Some devices could not be enumerated: {error}", error=detail),
                    8000,
                )
            elif result.pending_kinds:
                self.statusBar().showMessage(tr("Detecting devices…"), 3000)

    @Slot(str, object)
    def _on_engine_task_failed(self, kind: str, error: object) -> None:
        self._engine_tasks_inflight.discard(kind)
        if self._engine_closed:
            return
        detail = str(error)
        if kind == "activation":
            self.statusBar().showMessage(
                trf("Engine activation failed: {error}", error=detail), 5000
            )
            self._start_pending_activation()
        elif kind == "devices":
            self.statusBar().showMessage(
                trf("Could not refresh devices: {error}", error=detail),
                8000,
            )

    @Slot()
    def refresh_devices(self) -> None:
        self._load_device_catalogue(force_refresh=True)

    def _load_device_catalogue(self, *, force_refresh: bool = False) -> None:
        if self._submit_engine_task(
            "devices",
            partial(self.engine_client.device_catalogue, force_refresh=force_refresh),
        ):
            self.statusBar().showMessage(tr("Refreshing devices…"), 3000)

    def _start_pending_activation(self) -> None:
        if self._pending_activation is None or "activation" in self._engine_tasks_inflight:
            return
        snapshot, demand_roots = self._pending_activation
        self._pending_activation = None
        self._submit_engine_task(
            "activation",
            lambda: self.engine_client.activate(snapshot, demand_roots=demand_roots),
        )

    @Slot()
    def _activate_graph(self) -> None:
        if self._engine_closed:
            return
        snapshot = self.session.document.snapshot()
        self._pending_activation = (snapshot, self._runtime_demand_roots(snapshot))
        self._start_pending_activation()

    def _apply_engine_activation(self, activation: EngineActivation) -> None:
        if activation.activated:
            self._clear_runtime_previews()
            self.statusBar().showMessage(
                trf(
                    "Activated graph revision {revision}",
                    revision=activation.graph_revision,
                ),
                3000,
            )
            return
        count = len(activation.report.errors)
        self.statusBar().showMessage(
            trf(
                "Graph has {count} error(s); previous valid runtime remains active",
                count=count,
            ),
            5000,
        )

    def _runtime_demand_roots(self, snapshot: GraphSnapshot) -> tuple[UUID, ...]:
        include_images = self.image_preview_dock.isVisible()
        include_notes = self.note_preview_dock.isVisible()
        roots: set[UUID] = set()
        for node in snapshot.nodes:
            kind = self.registry.require(node.type_id).execution_kind
            if kind is ExecutionKind.SINK:
                roots.add(node.id)
            elif kind is ExecutionKind.VISUALIZER:
                # Note visualizers feed their own dock. Image/channel display
                # nodes remain demand anchors for the preview dock; the link
                # pills are anchored on producers instead (see below).
                if node.type_id == NOTE_VISUALIZER_TYPE_ID:
                    if include_notes:
                        roots.add(node.id)
                elif include_images:
                    roots.add(node.id)
        roots.update(self._pill_producer_roots(snapshot))
        return tuple(sorted(roots, key=str))

    def _pill_producer_roots(self, snapshot: GraphSnapshot) -> set[UUID]:
        """Return source nodes of visible image/channel/scalar link pills.

        A visible image, channel, or scalar value pill needs its producer to
        compute so the link can show live data, independent of whether the
        destination is a display node. A hidden pill must not force the engine
        to compute an unused producer chain.
        """
        pill_types = ("IMAGE", "CHANNEL", "INT", "FLOAT")
        type_by_connection = {
            view_model.connection_id: view_model.type_name
            for view_model in self.session.view_model.connections
        }
        roots: set[UUID] = set()
        for connection in snapshot.connections:
            if not bool(connection.ui_state.get(PREVIEW_VISIBLE_KEY, True)):
                continue
            if type_by_connection.get(connection.id) in pill_types:
                roots.add(connection.source_node_id)
        return roots

    @staticmethod
    def _image_visualizer_source_keys(
        snapshot: GraphSnapshot,
    ) -> frozenset[tuple[UUID, str]]:
        visualizer_ids = {
            node.id
            for node in snapshot.nodes
            if node.type_id in {DISPLAY_IMAGE_DATA_TYPE_ID, CHANNEL_DISPLAY_TYPE_ID}
        }
        return frozenset(
            (connection.source_node_id, connection.source_port_id)
            for connection in snapshot.connections
            if connection.destination_node_id in visualizer_ids
        )

    def _clear_runtime_previews(self) -> None:
        self._image_sequences.clear()
        self._note_sequences.clear()
        self._canvas_value_sequences.clear()
        self.scene.clear_connection_previews()
        self.image_preview_panel.clear_preview()
        self.note_preview_panel.clear_preview()

    @Slot(bool)
    def _on_image_preview_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self._image_sequences.clear()
        self._schedule_engine_activation()

    @Slot(bool)
    def _on_note_preview_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self._note_sequences.clear()
        self._schedule_engine_activation()

    @Slot()
    def _poll_previews(self) -> None:
        if self._engine_closed:
            return
        image_visible = self.image_preview_dock.isVisible()
        note_visible = self.note_preview_dock.isVisible()
        try:
            # Canvas link pills are always visible, so value and image previews are
            # polled unconditionally; only the note dock is gated on its own visibility.
            image_previews = self.engine_client.poll_image_previews(self._image_sequences)
            value_previews = self.engine_client.poll_value_previews(self._canvas_value_sequences)
            note_previews = (
                self.engine_client.poll_note_previews(self._note_sequences) if note_visible else ()
            )
        except (RuntimeError, TimeoutError):
            return
        for preview in image_previews:
            key = (preview.owner_id, preview.source_port_id)
            self._image_sequences[key] = preview.sequence
            self.scene.set_connection_image_preview(
                preview.owner_id, preview.source_port_id, image_preview_to_qimage(preview)
            )
            if image_visible and key in self._image_dock_preview_sources:
                self.image_preview_panel.show_preview(preview)
        for preview in value_previews:
            self._canvas_value_sequences[(preview.owner_id, preview.source_port_id)] = (
                preview.sequence
            )
            self.scene.set_connection_value_preview(
                preview.owner_id, preview.source_port_id, preview.text
            )
        for preview in note_previews:
            self._note_sequences[preview.owner_id] = preview.sequence
            self.note_preview_panel.show_preview(preview)

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
            self._engine_status.setText(tr("Engine RESTARTING"))
            return
        selected_nodes = self.scene.selected_node_ids()
        selected_node = next(iter(selected_nodes)) if len(selected_nodes) == 1 else None
        self._submit_engine_task(
            "refresh",
            partial(self._load_engine_refresh, selected_node),
        )

    def _load_engine_refresh(self, selected_node: UUID | None) -> _EngineRefreshSnapshot:
        metrics = self.engine_client.metrics()
        sources = self.engine_client.source_status()
        midi_outputs = self.engine_client.midi_output_status()
        diagnostic = None
        if selected_node is not None:
            diagnostics = self.engine_client.node_memory_diagnostics(selected_node)
            diagnostic = diagnostics[0] if diagnostics else None
        return _EngineRefreshSnapshot(metrics, sources, midi_outputs, diagnostic)

    def _apply_engine_refresh(self, refresh: _EngineRefreshSnapshot) -> None:
        metrics = refresh.metrics
        sources = refresh.sources
        midi_outputs = refresh.midi_outputs
        self.inspector.set_memory_diagnostic(refresh.diagnostic)
        source_text = ", ".join(tr(status.state.value) for status in sources) or tr("no source")
        source_errors = tuple(
            (status.node_id, status.last_error or tr("Unknown source error"))
            for status in sources
            if status.state is SourceState.ERROR
        )
        midi_errors = tuple(
            (
                status.node_id,
                status.last_error or tr("MIDI output is unavailable"),
                status.available_ports,
            )
            for status in midi_outputs
            if status.connection_state
            in {MidiOutputConnectionState.ERROR, MidiOutputConnectionState.UNAVAILABLE}
        )
        runtime_errors = tuple(
            (error.node_id, error.code, error.message) for error in metrics.runtime_errors
        )
        diagnostic_lines = [
            trf("Source {node}: {message}", node=str(node_id)[:8], message=message)
            for node_id, message in source_errors
        ]
        diagnostic_lines.extend(
            trf(
                "MIDI {node}: {message}. Available outputs: {outputs}",
                node=str(node_id)[:8],
                message=message,
                outputs=", ".join(ports) or tr("none"),
            )
            for node_id, message, ports in midi_errors
        )
        diagnostic_lines.extend(
            trf(
                "Node {node}: {message} ({code})",
                node=str(node_id)[:8],
                message=message,
                code=code,
            )
            for node_id, code, message in runtime_errors
        )
        if diagnostic_lines:
            diagnostic_detail = "\n".join(diagnostic_lines)
            self._engine_status.setToolTip(diagnostic_detail)
            if (
                source_errors != self._source_error_signature
                or midi_errors != self._midi_error_signature
                or runtime_errors != self._runtime_error_signature
            ):
                self.statusBar().showMessage(diagnostic_detail, 10000)
        else:
            midi_detail = "\n".join(
                trf(
                    "MIDI {port}: {state}, {count} active note(s)",
                    port=status.selected_port or tr("unselected"),
                    state=tr(status.connection_state.value),
                    count=status.active_note_count,
                )
                for status in midi_outputs
            )
            telemetry = (
                f"Input / processed / preview: {metrics.input_fps:.1f} / "
                f"{metrics.processed_fps:.1f} / {metrics.preview_fps:.1f} FPS\n"
                f"Graph p95: {metrics.p95_graph_execution_ms:.2f} ms · "
                f"queue {metrics.mailbox_occupancy}/{metrics.mailbox_capacity} · "
                f"frame age {metrics.frame_age_ms:.1f} ms\n"
                f"CPU {metrics.cpu_percent:.1f}% · "
                f"RAM {metrics.memory_bytes / (1024 * 1024):.1f} MiB"
            )
            self._engine_status.setToolTip(
                f"{midi_detail}\n{telemetry}" if midi_detail else telemetry
            )
        self._source_error_signature = source_errors
        self._midi_error_signature = midi_errors
        self._runtime_error_signature = runtime_errors
        midi_text = ", ".join(tr(status.connection_state.value) for status in midi_outputs) or tr(
            "no output"
        )
        summary = (
            trf(
                "Engine {state} · source {source}",
                state=tr(metrics.state.value),
                source=source_text,
            )
            + " · "
            f"MIDI {midi_text} · "
            f"{metrics.processed_ticks} ticks"
        )
        if metrics.dropped_before_processing:
            summary += trf(
                " · {count} dropped",
                count=metrics.dropped_before_processing,
            )
        self._engine_status.setText(summary)

    @Slot(bool)
    def _on_profiler_visibility_changed(self, visible: bool) -> None:
        if not self._engine_closed:
            try:
                self.engine_client.set_profiling_enabled(visible)
            except (RuntimeError, TimeoutError):
                self.statusBar().showMessage(tr("Could not change runtime profiling state"), 3000)
                return
        if visible and not self._engine_closed:
            self._profiler_timer.start()
            self._refresh_profiles()
            return
        self._profiler_timer.stop()
        self.scene.set_node_heatmap(None)

    @Slot()
    def _refresh_profiles(self) -> None:
        if self._engine_closed or not self.profiler_dock.isVisible() or self.profiler_panel.frozen:
            return
        try:
            profiles = self.engine_client.node_profiles()
        except (RuntimeError, TimeoutError):
            return
        names = {node.node_id: node.title for node in self.session.view_model.nodes}
        if self.profiler_panel.set_profiles(profiles, names):
            self._update_profiler_heatmap(self.profiler_panel.heatmap_checkbox.isChecked())

    @Slot()
    def _reset_profiling(self) -> None:
        if self._engine_closed:
            return
        try:
            self.engine_client.reset_profiling()
        except (RuntimeError, TimeoutError):
            self.statusBar().showMessage(tr("Could not reset runtime profiling"), 3000)
            return
        self.scene.set_node_heatmap(None)
        self.statusBar().showMessage(tr("Runtime profiling reset"), 2000)

    @Slot(bool)
    def _update_profiler_heatmap(self, enabled: bool) -> None:
        levels = profile_heat_levels(self.profiler_panel.profiles) if enabled else None
        self.scene.set_node_heatmap(levels)

    def _show_engine_failure(self, status: EngineStatus) -> None:
        state = status.connection_state.value
        exit_text = "" if status.exit_code is None else f" · exit {status.exit_code}"
        crash_report = (
            None
            if status.crash_log_path is None or not Path(status.crash_log_path).is_file()
            else status.crash_log_path
        )
        log_text = (
            tr(" · no crash report file")
            if crash_report is None
            else trf(" · crash report {path}", path=crash_report)
        )
        self._engine_status.setText(
            trf(
                "Engine {state} · STOPPED{exit_text}{log_text}",
                state=tr(state),
                exit_text=exit_text,
                log_text=log_text,
            )
        )
        detail = status.last_error or tr("The engine process stopped unexpectedly.")
        self.statusBar().showMessage(
            trf("Engine {state}: {detail}", state=tr(state.lower()), detail=detail)
        )
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
        message = trf(
            "The engine is {state}. The graph document remains open and editable.\n\n"
            "Details: {detail}",
            state=tr(state.lower()),
            detail=detail,
        )
        if status.exit_code is not None:
            message += trf("\nExit code: {code}", code=status.exit_code)
        if crash_report is not None:
            message += trf("\nCrash report: {path}", path=crash_report)
        else:
            message += tr("\nNo crash-report file was produced.")
            message += tr(" Native termination can occur before Python can write a traceback.")
        message += tr("\n\nUse Graph → Restart Engine to rebuild the latest valid runtime.")
        QMessageBox.critical(self, tr("Engine stopped"), message)

    @Slot()
    def export_diagnostic_bundle(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(
            self,
            tr("Export Diagnostic Bundle"),
            str(self.paths.data / "synmachine-diagnostics.zip"),
            tr("ZIP archives (*.zip)"),
        )
        if not destination:
            return
        include_paths = (
            QMessageBox.question(
                self,
                tr("Include filesystem paths?"),
                tr(
                    "Filesystem paths can contain personal information. "
                    "Include them in this bundle?\n\n"
                    "Choose No for the recommended redacted bundle."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )
        metrics = None
        profiles = ()
        if not self._engine_closed:
            try:
                metrics = self.engine_client.metrics()
                profiles = self.engine_client.node_profiles()
            except (RuntimeError, TimeoutError):
                pass
        try:
            result = create_diagnostic_bundle(
                destination,
                self.session.document.snapshot(),
                logs_directory=self.paths.logs,
                engine_metrics=metrics,
                node_profiles=profiles,
                include_paths=include_paths,
            )
        except OSError as error:
            self._show_error("Could not export diagnostics", str(error))
            return
        privacy = tr("with paths" if include_paths else "with paths redacted")
        self.statusBar().showMessage(
            trf("Exported diagnostic bundle {privacy}: {path}", privacy=privacy, path=result.path),
            6000,
        )

    @Slot()
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            tr("About Synesthesia Machine"),
            f"Synesthesia Machine {__version__}\n\n"
            + tr("Video and image processing mapped to visualized MIDI state."),
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
        self.autosave_controller.request(
            self.session.document.snapshot(),
            explicit_path=self.session.current_path,
        )

    @Slot(object)
    def _on_autosave_saved(self, path: object) -> None:
        if isinstance(path, Path):
            self.statusBar().showMessage(
                trf("Recovery saved to {name}", name=path.name),
                3500,
            )

    @Slot(object)
    def _on_autosave_failed(self, error: object) -> None:
        self.statusBar().showMessage(trf("Autosave failed: {error}", error=error), 5000)

    def _discard_recovery(self, document_id: UUID) -> None:
        self.autosave_controller.discard(document_id)

    def _confirm_document_replacement(self) -> _ReplacementDecision:
        if not self.session.is_dirty:
            return _ReplacementDecision.PROCEED
        self._autosave()
        choice = QMessageBox.warning(
            self,
            tr("Unsaved graph"),
            tr("Save changes before continuing?"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return (
                _ReplacementDecision.PROCEED
                if self.save_document()
                else _ReplacementDecision.CANCEL
            )
        if choice == QMessageBox.StandardButton.Discard:
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
                tr("Recover autosaved graph"),
                trf(
                    "A newer autosave was found. Restore it now?\n\nRecovery: {name}",
                    name=record.path.name,
                )
                + (
                    trf("\nOriginal: {path}", path=explicit_path)
                    if explicit_path is not None
                    else ""
                ),
                QMessageBox.StandardButton.Open
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Open,
            )
            if choice == QMessageBox.StandardButton.Discard:
                self._discard_recovery(record.document_id)
                continue
            if choice != QMessageBox.StandardButton.Open:
                return
            try:
                self.session.recover_document(record.path, explicit_path=explicit_path)
            except (GraphPersistenceError, OSError, ValueError) as error:
                self._show_error("Could not recover graph", str(error))
                return
            self.statusBar().showMessage(trf("Recovered {name}", name=record.path.name), 5000)
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
            empty = QAction(tr("No recent graphs"), self.recent_menu)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for index, path in enumerate(self._recent_paths, start=1):
            action = QAction(f"&{index} {path.name}", self.recent_menu)
            action.setToolTip(str(path))
            action.triggered.connect(partial(self.open_path, path))
            self.recent_menu.addAction(action)
        self.recent_menu.addSeparator()
        clear_action = QAction(tr("&Clear Recent Graphs"), self.recent_menu)
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
        language_changed = preferences.language is not self.preferences.language
        self.preferences = preferences
        self.settings_store.save_preferences(preferences)
        if language_changed:
            set_language(preferences.language)
        self._autosave_timer.setInterval(preferences.autosave_delay_seconds * 1000)
        self.scene.configure_grid_snap(
            enabled=preferences.grid_snap_enabled,
            spacing=preferences.grid_size,
        )
        self._recent_paths = self._recent_paths[: preferences.recent_file_limit]
        self.settings.setValue("recentFiles", [str(path) for path in self._recent_paths])
        if language_changed:
            self._retranslate_ui()
        else:
            self._refresh_recent_menu()

    def _retranslate_ui(self) -> None:
        """Apply a new English/French preference without rebuilding the document session."""

        self.setAccessibleName(tr("Synesthesia Machine graph editor"))
        for dock, source in (
            (self.library_dock, "Node Library"),
            (self.inspector_dock, "Inspector"),
            (self.image_preview_dock, "Image Preview"),
            (self.note_preview_dock, "Note Visualizer"),
            (self.profiler_dock, "Runtime Profiler"),
            (self.issues_dock, "Validation Issues"),
        ):
            dock.setWindowTitle(tr(source))
        self.recent_menu.setTitle(tr("Open &Recent"))
        self.transport_toolbar.setWindowTitle(tr("Transport"))
        self.transport_toolbar.setAccessibleName(tr("Source transport and output silence"))
        for key, source in (
            ("file", "&File"),
            ("edit", "&Edit"),
            ("view", "&View"),
            ("graph", "&Graph"),
            ("arrange", "&Arrange Selection"),
            ("outputs", "&Outputs"),
            ("help", "&Help"),
        ):
            self._menus[key].setTitle(tr(source))
        self.action_registry.retranslate()
        for action in self.action_registry.values():
            button = self.transport_toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setAccessibleName(action.text().replace("&", ""))
        self.library.retranslate()
        self.scene.sync_from_session()
        self.inspector.retranslate()
        self.issues_panel.set_report(self.session.report)
        self.image_preview_panel.retranslate()
        self.note_preview_panel.retranslate()
        self.profiler_panel.retranslate()
        self._engine_status.setAccessibleName(tr("Engine runtime status"))
        self.statusBar().showMessage(tr("Ready"))
        self._refresh_recent_menu()
        self._refresh_document_ui()
        self._refresh_selection_ui()

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
        QMessageBox.critical(self, tr(title), detail)
        self.statusBar().showMessage(detail, 5000)

    def closeEvent(self, event: QCloseEvent) -> None:
        decision = self._confirm_document_replacement()
        if decision is not _ReplacementDecision.CANCEL:
            if decision is _ReplacementDecision.DISCARD:
                self._discard_recovery(self.session.document.document_id)
            self._activation_timer.stop()
            self._autosave_timer.stop()
            self._preview_timer.stop()
            self._metrics_timer.stop()
            self._profiler_timer.stop()
            self._device_refresh_timer.stop()
            if not self._engine_closed:
                self._engine_closed = True
                self._pending_activation = None
                self._engine_executor.shutdown(wait=True, cancel_futures=True)
                with suppress(RuntimeError, TimeoutError):
                    self.engine_client.close()
            self.autosave_controller.close()
            self._save_window_state()
            event.accept()
            return
        event.ignore()
