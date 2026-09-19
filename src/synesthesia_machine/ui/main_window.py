"""Graph editor compositor: renders document state and forwards engine intents.

The window is a thin compositor over the document session and the
:class:`~synesthesia_machine.ui.engine_bridge.EngineBridge`. User intents
(transport, activation, profiling, device refresh) are forwarded to the bridge,
which owns the engine client; the window renders the state the bridge publishes
(scene, preview panels, status bar, inspector, profiler).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QPointF,
    QSettings,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QKeySequence
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
    QWidget,
)

from synesthesia_machine import __version__
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineClient,
    EngineStatus,
    ImagePreview,
    NodeProfile,
    NotePreview,
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
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.persistence import (
    RelinkMatch,
    find_missing_media,
    fragment_from_json,
    fragment_to_json,
    verify_relink_candidate,
)
from synesthesia_machine.persistence.autosave import AutosaveStore, RecoveryRecord
from synesthesia_machine.runtime import EngineSession, midi_export_eligibility
from synesthesia_machine.ui.actions import ActionRegistry, ActionSpec
from synesthesia_machine.ui.application_settings import (
    ApplicationSettingsStore,
    EditorPreferences,
    PreferencesDialog,
)
from synesthesia_machine.ui.autosave_controller import AutosaveController
from synesthesia_machine.ui.canvas import GraphScene, GraphView
from synesthesia_machine.ui.demand_roots import compute_demand_roots
from synesthesia_machine.ui.document_lifecycle import (
    DocumentLifecycleController,
    ReplacementDecision,
)
from synesthesia_machine.ui.engine_bridge import (
    EngineBridge,
    EngineBridgeState,
    EngineRestartOutcome,
)
from synesthesia_machine.ui.engine_status_presenter import (
    EngineStatusPresenter,
    ErrorTooltip,
    FailureIntent,
    TelemetryIntents,
)
from synesthesia_machine.ui.engine_task_runner import QtEngineTaskRunner, QtUiClock
from synesthesia_machine.ui.midi_export import (
    MidiExportJob,
    midi_export_failure_text,
    midi_export_tooltip,
)
from synesthesia_machine.ui.preview_families import display_visualizer
from synesthesia_machine.ui.preview_router import RoutingResult
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
        self.theme = theme
        self.settings = settings or QSettings("Synesthesia Machine", "Synesthesia Machine")
        self.settings_store = ApplicationSettingsStore(self.settings)
        self.preferences = self.settings_store.load_preferences()
        set_language(self.preferences.language)
        self.session = DocumentSession(registry, self)
        self.autosave_store = AutosaveStore(paths.recovery)
        self.autosave_controller = AutosaveController(self.autosave_store, self, self.session)
        self.action_registry = ActionRegistry(self)
        self.scene = GraphScene(self.session, theme, self)
        self.scene.configure_grid_snap(
            enabled=self.preferences.grid_snap_enabled,
            spacing=self.preferences.grid_size,
        )
        self.view = GraphView(self.scene, theme)
        self.library = NodeLibrary(registry, self)
        self.inspector = InspectorPanel(self.session, self)
        self.inspector.videoSeekRequested.connect(self._on_inspector_video_seek)
        self.issues_panel = ValidationIssuePanel(self)
        self.image_preview_panel = ImagePreviewPanel(self)
        self.note_preview_panel = NotePreviewPanel(self)
        self.profiler_panel = ProfilerPanel(self)
        self.recent_menu = QMenu(tr("Open &Recent"), self)
        self.document_lifecycle = DocumentLifecycleController(
            host=self,
            session=self.session,
            settings_store=self.settings_store,
            autosave_store=self.autosave_store,
            autosave_controller=self.autosave_controller,
            recent_file_limit=lambda: self.preferences.recent_file_limit,
        )
        self._engine_closed = False
        self._midi_eligibility: tuple[bool, str] | None = None
        self._midi_eligibility_key: object | None = None
        self._source_node_ids: tuple[UUID, ...] = ()
        self._source_node_ids_key: object | None = None
        self._midi_export_job: MidiExportJob | None = None
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
        # The engine bridge owns the engine session (the Qt-free module that
        # drives the engine client: preview cursors, the folded connection
        # state machine, the known-stopped cache, the restart policy) and the
        # Qt-facing dispatch over the task pool; the window only composes the
        # state the bridge publishes.
        self._engine_task_runner = QtEngineTaskRunner(self)
        self._engine_tasks_inflight = self._engine_task_runner.inflight
        self.engine_session = EngineSession(engine_client)
        self.engine_bridge = EngineBridge(
            self.engine_session,
            demand_roots_for=self._runtime_demand_roots,
            task_runner=self._engine_task_runner,
            clock=QtUiClock(self._activation_timer),
            on_previews=self._on_pumped_previews,
            on_state=self._on_engine_state,
            on_status_message=self._on_engine_status_message,
        )
        # The presenter interprets the bridge's published state into display
        # intents (which settled facts are new, which error digests changed);
        # the window renders the intents and owns only localization and
        # widget policy.
        self.engine_status_presenter = EngineStatusPresenter()
        self._device_refresh_timer.timeout.connect(self.engine_bridge.request_device_catalogue)
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
        self._shortcut_labels: dict[str, str] = {}
        self._shortcut_defaults: dict[str, str] = {}
        self._snapshot_default_shortcuts()
        self._apply_shortcuts()
        self._create_status_bar()
        self._connect_signals()
        self._restore_window_state()
        # Profiling is opt-in every launch even when an older saved layout left the dock visible.
        self.profiler_dock.hide()
        self.document_lifecycle.refresh_recent_menu()
        self._refresh_document_ui()
        self._refresh_selection_ui()
        self._preview_timer.start()
        self._metrics_timer.start()
        # Let initial engine status/activation settle before the first hardware catalogue request.
        # The owned timer is cancelled with the window; users can refresh immediately from the menu.
        self._device_refresh_timer.start()
        if offer_recovery:
            QTimer.singleShot(0, self.document_lifecycle.offer_recovery)

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
        create(
            ActionSpec(
                "export_midi",
                "Export &MIDI…",
                "Simulate the video-only graph end to end and save a Standard MIDI File",
            ),
            self.export_midi,
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
                "select_all",
                "Select &All",
                "Select all graph objects",
                QKeySequence.StandardKey.SelectAll,
            ),
            self.select_all_nodes,
        )
        create(
            ActionSpec(
                "frame_selection", "Frame &Selection", "Frame the selected graph objects", "F"
            ),
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
            ("align_right", "Align Right", AlignMode.RIGHT),
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
        file_menu.addAction(self.action_registry.require("export_midi"))
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
        self.session.runtimeChanged.connect(self._schedule_engine_activation)
        self.session.pathChanged.connect(self._refresh_document_ui)
        self.session.dirtyChanged.connect(self._on_dirty_changed)
        self.session.validationChanged.connect(self.issues_panel.set_report)
        self.issues_panel.issueActivated.connect(self._focus_validation_issue)
        self.scene.selectionChanged.connect(self._refresh_selection_ui)
        self.scene.connectionDroppedOnEmpty.connect(self._search_compatible_node)
        self.scene.connectionInspectRequested.connect(self._inspect_connection)
        self.view.requestSearch.connect(self._search_nodes)
        # Dropping a saved graph file opens it with the exact File > Open flow
        # (dirty-document confirmation, recovery handling, recent files, status).
        self.view.openGraphFileRequested.connect(self._open_graph_file_from_drop)
        self.library.nodeActivated.connect(self._add_library_node)
        self._autosave_timer.timeout.connect(self.document_lifecycle.autosave_now)
        # The activation timer's timeout is owned by the bridge's clock.
        self._preview_timer.timeout.connect(self._poll_previews)
        self.image_preview_dock.visibilityChanged.connect(self._on_image_preview_visibility_changed)
        self.note_preview_dock.visibilityChanged.connect(self._on_note_preview_visibility_changed)
        self._metrics_timer.timeout.connect(self._refresh_engine_status)
        self._profiler_timer.timeout.connect(self._refresh_profiles)
        self.profiler_dock.visibilityChanged.connect(self._on_profiler_visibility_changed)
        self.profiler_panel.resetRequested.connect(self._reset_profiling)
        self.profiler_panel.heatmapChanged.connect(self._update_profiler_heatmap)
        QApplication.clipboard().dataChanged.connect(self._refresh_action_states)
        self.autosave_controller.saved.connect(self._on_autosave_saved)
        self.autosave_controller.failed.connect(self._on_autosave_failed)

    @Slot()
    def play(self) -> None:
        # The source-state lookup and the play/resume command are engine IPC:
        # the bridge runs them on the engine task pool so a slow or hung child
        # cannot block the UI event thread.
        target = self._transport_target()
        if target is None:
            return
        self.engine_bridge.play_or_resume(target)

    @Slot()
    def pause(self) -> None:
        # Runs on the engine task pool (see play()); the UI thread only
        # resolves the target. A command issued while another is in flight
        # is dropped by the per-kind coalescing guard.
        target = self._transport_target()
        if target is None:
            return
        self.engine_bridge.pause(target)

    @Slot()
    def stop(self) -> None:
        target = self._transport_target()
        if target is None:
            return
        self.engine_bridge.stop_source(target)

    @Slot()
    def reload(self) -> None:
        target = self._transport_target()
        if target is None:
            return
        self.engine_bridge.reload(target)

    @Slot(object, float)
    def _on_inspector_video_seek(self, node_id: object, position_s: float) -> None:
        """Seek the Load Video source shown in the inspector (engine IPC)."""

        if self._engine_closed or not isinstance(node_id, UUID):
            return
        self.engine_bridge.seek(node_id, position_s)

    @Slot()
    def panic(self) -> None:
        # Panic waits for the MIDI output services to confirm all-notes-off
        # (up to their timeout): it must not block the UI event thread.
        self.engine_bridge.panic()

    @Slot()
    def restart_engine(self) -> None:
        # A restart is the heaviest engine IPC (stop handshake, child
        # spawn, start handshake, re-activation) and runs on the engine
        # task pool; the UI thread only updates the status bar. The engine
        # session rebuilds the last valid graph — never a possibly-broken
        # current draft (ADR-0013/0020 policy).
        if self._engine_closed:
            return
        if "restart" in self._engine_tasks_inflight:
            return
        self.action_registry.require("restart_engine").setEnabled(False)
        self.statusBar().showMessage(tr("Restarting engine…"))
        self.engine_bridge.restart()

    def _transport_target(self) -> UUID | None:
        sources = tuple(node.node_id for node in self.session.view_model.nodes if node.is_source)
        resolution = resolve_transport_target(sources, self.scene.selected_node_ids())
        if resolution.target is None:
            self.statusBar().showMessage(tr(resolution.message), 4000)
        return resolution.target

    @Slot()
    def new_document(self) -> None:
        self.document_lifecycle.new_document()

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
            decision = self.document_lifecycle.confirm_replacement()
            if decision is ReplacementDecision.CANCEL:
                return
            try:
                snapshot = generate_random_graph(self.registry)
            except (KeyError, RuntimeError, ValueError) as error:
                self._show_error("Could not generate random graph", str(error))
                return
            self.session.replace_with_snapshot(snapshot)
            if decision is ReplacementDecision.DISCARD:
                self.document_lifecycle.discard_recovery(previous_id)
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
        self.document_lifecycle.open_interactive()

    def open_path(self, path: Path) -> bool:
        return self.document_lifecycle.open_path(path)

    # -- Harness driver / observation API ---------------------------------
    # Stable public surface for the diagnostic and benchmark tools to drive the
    # window, so they stop reaching into panels, docks, widgets, and the
    # session view model directly.

    def reveal_preview_docks(self) -> None:
        self.image_preview_dock.show()
        self.note_preview_dock.show()

    def frame_all(self) -> None:
        self.view.frame_all()

    def node_titles(self) -> Mapping[UUID, str]:
        return {node.node_id: node.title for node in self.session.view_model.nodes}

    def latest_image_preview(self) -> ImagePreview | None:
        return self.image_preview_panel.image_widget.latest_preview

    def latest_note_preview(self) -> NotePreview | None:
        return self.note_preview_panel.note_widget.latest_preview

    def push_previews(
        self,
        image_previews: Iterable[ImagePreview],
        note_previews: Iterable[NotePreview],
    ) -> None:
        for preview in image_previews:
            self.image_preview_panel.show_preview(preview)
        for preview in note_previews:
            self.note_preview_panel.show_preview(preview)

    @Slot(Path)
    def _open_graph_file_from_drop(self, path: Path) -> None:
        # Runs synchronously inside the view's drop event (direct signal
        # connection); report the outcome so the OS drop is accepted only
        # when the graph actually opened.
        self.view.set_drop_open_result(self.open_path(path))

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
        return self.document_lifecycle.save()

    @Slot()
    def save_document_as(self) -> bool:
        return self.document_lifecycle.save_as()

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
        target = display_visualizer(connection.type_name)
        if target is not None:
            type_id, input_port, dock_key = target
            dock = {"image": self.image_preview_dock, "note": self.note_preview_dock}[dock_key]
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
        view_model = self.session.view_model
        self._node_count.setText(
            trf(
                "{nodes} node(s) · {cables} cable(s) · {groups} group/comment(s)",
                nodes=len(view_model.nodes),
                cables=len(view_model.connections),
                groups=len(view_model.groups),
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
        selected_nodes = self.scene.selected_node_ids()
        has_nodes = bool(selected_nodes)
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
        selected_node_count = len(selected_nodes)
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
        # The source list and the MIDI export eligibility depend only on the
        # document and its validation report, both of which change atomically
        # in session._refresh. The report object is replaced on every full
        # refresh (and kept for presentation-only refreshes, which leave the
        # document untouched), so its identity is a sound cache key: selection
        # and clipboard events no longer pay the O(N) scan or the per-video
        # file-system stats.
        # A monotonic revision (bumped by every session refresh, full or
        # presentation-only) is the cache key; object identity would be
        # unsafe because CPython reuses addresses of freed report objects.
        cache_key = self.session.revision
        if self._source_node_ids_key is not cache_key:
            self._source_node_ids = tuple(
                node.node_id for node in self.session.view_model.nodes if node.is_source
            )
            eligible, reason = midi_export_eligibility(
                self.session.document.snapshot(),
                self.registry,
                graph_valid=not self.session.report.errors,
            )
            self._midi_eligibility = (eligible, reason)
            self._midi_eligibility_key = cache_key
            self._source_node_ids_key = cache_key
        assert self._midi_eligibility is not None
        eligible, reason = self._midi_eligibility
        transport = resolve_transport_target(self._source_node_ids, selected_nodes)
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
                    target=tr("selected" if transport.target in selected_nodes else "only"),
                )
                if transport.target is not None
                else tr(transport.message)
            )
        export_action = self.action_registry.require("export_midi")
        export_action.setEnabled(eligible)
        tooltip = (
            midi_export_tooltip(reason)
            if not eligible
            else tr("Simulate the video-only graph end to end and save a Standard MIDI File")
        )
        export_action.setToolTip(tooltip)
        export_action.setStatusTip(tooltip)

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
        if self._engine_closed:
            return
        self.engine_bridge.schedule_activation(self.session.document.snapshot())

    @Slot()
    def _activate_graph(self) -> None:
        # The timer path debounces through the bridge's clock; this entry
        # point (also used directly by tests) flushes the activation now.
        if self._engine_closed:
            return
        self.engine_bridge.activate_now(self.session.document.snapshot())

    @Slot()
    def refresh_devices(self) -> None:
        self.engine_bridge.request_device_catalogue(force_refresh=True)

    def _apply_engine_activation(self, activation: EngineActivation) -> None:
        # A (re)activation may have started, stopped, or failed the engine;
        # the session dropped its known-stopped cache on activation, so the
        # next status tick re-learns the real state.
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
        self._clear_runtime_previews()
        # The client reset its own preview state as part of the activation
        # (the engine auto-stopped); the window only clears its widgets.
        self.statusBar().showMessage(
            trf("Graph has {count} error(s); engine stopped", count=count),
            5000,
        )

    def _runtime_demand_roots(self, snapshot: GraphSnapshot) -> tuple[UUID, ...]:
        # The callback contract passes the current snapshot; the session's
        # view model is the authoritative projection of the same revision, so
        # the demand policy reads it and never re-derives conventions here.
        del snapshot
        return compute_demand_roots(
            self.session.view_model,
            image_dock_visible=self.image_preview_dock.isVisible(),
            note_dock_visible=self.note_preview_dock.isVisible(),
        )

    def _clear_runtime_previews(self) -> None:
        """Clear the UI's preview widgets only.

        The engine client owns its retained preview state and resets it on
        every engine transition (activation, restart, auto-stop, shutdown);
        the window must not reach into it, because the process client's
        preview slots may already serve the new generation by the time this
        runs.
        """
        self.scene.clear_connection_previews()
        self.image_preview_panel.clear_preview()
        self.note_preview_panel.clear_preview()

    @Slot(bool)
    def _on_image_preview_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self.engine_bridge.image_preview_hidden()
        self._schedule_engine_activation()

    @Slot(bool)
    def _on_note_preview_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self.engine_bridge.note_preview_hidden()
        self._schedule_engine_activation()

    @Slot()
    def _poll_previews(self) -> None:
        if self._engine_closed:
            return
        # Canvas link pills are always visible, so value and image previews
        # are polled unconditionally; the note dock's port is gated on its
        # visibility. The pump routes the batch itself, so this call hands
        # it the image dock's visibility and the current projection (its
        # frames still land on the canvas pills either way).
        self.engine_bridge.pump_previews_once(
            note_visible=self.note_preview_dock.isVisible(),
            image_visible=self.image_preview_dock.isVisible(),
            view=self.session.view_model,
        )

    def _on_pumped_previews(self, routed: RoutingResult) -> None:
        """Apply the bridge's routing result to the pills and docks (the
        window performs no routing itself)."""
        dock_keys = {(preview.owner_id, preview.source_port_id) for preview in routed.image_dock}
        for preview in routed.image_pill:
            # Convert the preview bytes once and share the QImage between the
            # canvas link pill and the dock instead of wrapping+copying twice.
            qimage = image_preview_to_qimage(preview)
            self.scene.set_connection_image_preview(
                preview.owner_id, preview.source_port_id, qimage
            )
            if (preview.owner_id, preview.source_port_id) in dock_keys:
                self.image_preview_panel.show_image(qimage, preview)
        for preview in routed.value_pill:
            self.scene.set_connection_value_preview(
                preview.owner_id, preview.source_port_id, preview.text
            )
        for preview in routed.note_dock:
            self.note_preview_panel.show_preview(preview)

    @Slot()
    def _refresh_engine_status(self) -> None:
        # The cheap status liveness check runs on the UI thread through the
        # bridge (a local fold in the process client, no IPC); the presenter
        # then decides whether the heavy metrics round trip (engine task
        # pool) is worth it for the published state.
        if self._engine_closed:
            return
        self.engine_bridge.refresh_status()
        plan = self.engine_status_presenter.plan_telemetry_refresh(
            self.engine_bridge.state, self.scene.selected_node_ids()
        )
        if plan.skip:
            # A stopped engine publishes no data, and the cheap liveness
            # check above still catches crash and heartbeat timeouts.
            # Skipping the periodic metrics round trip keeps the UI thread
            # free for editing.
            return
        self.engine_bridge.refresh_telemetry(plan.node)

    def _render_engine_status(self, status: EngineStatus) -> None:
        intent = self.engine_status_presenter.present_engine_status(status)
        self.action_registry.require("restart_engine").setEnabled(intent.restart_enabled)
        if intent.restart_enabled:
            # A failed engine: the presenter owns the dialog dedup (failure
            # signature); the window renders the label and status-bar text
            # and shows the dialog only for a new failure.
            self._show_engine_failure(
                status, self.engine_status_presenter.present_engine_failure(status)
            )
            return
        # Recovery: the presenter forgets the announced failure so a later
        # crash dialog is shown again.
        self.engine_status_presenter.failure_reset()
        if intent.restarting:
            self._engine_status.setText(tr("Engine RESTARTING"))

    def _apply_telemetry_intents(self, intents: TelemetryIntents) -> None:
        """Apply the presenter's display intents for one telemetry publish."""
        self.session.set_source_statuses(intents.source_statuses)
        self.inspector.set_memory_diagnostic(intents.diagnostic)
        self.inspector.set_source_statuses(intents.source_statuses)
        source_text = ", ".join(tr(state) for state in intents.source_states) or tr("no source")
        tooltip = intents.tooltip
        if isinstance(tooltip, ErrorTooltip):
            lines = [
                trf(
                    "Source {node}: {message}",
                    node=str(node_id)[:8],
                    message=message or tr("Unknown source error"),
                )
                for node_id, message in tooltip.sources
            ]
            lines.extend(
                trf(
                    "MIDI {node}: {message}. Available outputs: {outputs}",
                    node=str(node_id)[:8],
                    message=message or tr("MIDI output is unavailable"),
                    outputs=", ".join(ports) or tr("none"),
                )
                for node_id, message, ports in tooltip.midi
            )
            lines.extend(
                trf(
                    "Node {node}: {message} ({code})",
                    node=str(node_id)[:8],
                    message=message,
                    code=code,
                )
                for node_id, code, message in tooltip.nodes
            )
            detail = "\n".join(lines)
            self._engine_status.setToolTip(detail)
            if intents.announce_errors:
                self.statusBar().showMessage(detail, 10000)
        else:
            midi_detail = "\n".join(
                trf(
                    "MIDI {port}: {state}, {count} active note(s)",
                    port=port or tr("unselected"),
                    state=tr(state),
                    count=count,
                )
                for port, state, count in tooltip.midi
            )
            figures = tooltip.figures
            telemetry = (
                f"Input / processed / preview: {figures[0]} / "
                f"{figures[1]} / {figures[2]} FPS\n"
                f"Graph p95: {figures[3]} ms · queue {figures[4]} · "
                f"frame age {figures[5]} ms\n"
                f"CPU {figures[6]}% · RAM {figures[7]} MiB"
            )
            self._engine_status.setToolTip(
                f"{midi_detail}\n{telemetry}" if midi_detail else telemetry
            )
        midi_text = ", ".join(tr(state) for state in intents.midi_states) or tr("no output")
        summary = (
            trf(
                "Engine {state} · source {source}",
                state=tr(intents.engine_state),
                source=source_text,
            )
            + " · "
            f"MIDI {midi_text} · "
            f"{intents.ticks} ticks"
        )
        if intents.dropped:
            summary += trf(" · {count} dropped", count=intents.dropped)
        # The status line is rebuilt every 100 ms while the engine runs;
        # skip the setText re-layout when nothing changed so an idle engine
        # costs only the string comparison.
        if self._engine_status.text() != summary:
            self._engine_status.setText(summary)

    def _apply_device_catalogue(self, catalogue: DeviceCatalogue) -> None:
        self.session.set_device_catalogue(catalogue)
        if catalogue.pending_kinds:
            self._device_refresh_timer.start(250)
        if catalogue.errors:
            detail = "; ".join(f"{kind.value}: {message}" for kind, message in catalogue.errors)
            self.statusBar().showMessage(
                trf("Some devices could not be enumerated: {error}", error=detail),
                8000,
            )
        elif catalogue.pending_kinds:
            self.statusBar().showMessage(tr("Detecting devices…"), 3000)

    def _apply_engine_restart(self, outcome: EngineRestartOutcome) -> None:
        if outcome.outcome != "restart_failed":
            # The child was replaced: forget its previews and the presenter's
            # announced failure (re-arming the dialog for a later crash); the
            # session dropped its "known stopped" cache on restart, so the
            # final refresh below re-learns the real state instead of
            # skipping it.
            self._clear_runtime_previews()
            self.engine_status_presenter.failure_reset()
        if outcome.outcome == "restart_failed":
            self.statusBar().showMessage(
                trf("Engine restart failed: {error}", error=outcome.detail), 8000
            )
        elif outcome.outcome == "rebuild_failed":
            self.statusBar().showMessage(
                trf(
                    "Engine restarted but graph rebuild failed: {error}",
                    error=outcome.detail,
                ),
                8000,
            )
        elif outcome.outcome == "rejected":
            self.statusBar().showMessage(
                tr("Engine restarted; current invalid graph was not activated"), 8000
            )
        else:
            self.statusBar().showMessage(tr("Engine restarted"), 5000)
        self._refresh_engine_status()

    def _on_engine_state(self, state: EngineBridgeState) -> None:
        """Render the bridge's published state (called on the UI thread)."""
        if self._engine_closed:
            return
        if state.status is not None:
            self._render_engine_status(state.status)
        # The presenter folds the published state into per-concern intents;
        # its object-identity caches tell a new settled fact (the last_*
        # fields persist in the state) from a stale one carried along by an
        # unrelated publish.
        intents = self.engine_status_presenter.present_state(state)
        if intents.telemetry is not None:
            self._apply_telemetry_intents(intents.telemetry)
        if intents.device_catalogue is not None:
            self._apply_device_catalogue(intents.device_catalogue)
        if intents.node_profiles is not None and self.profiler_dock.isVisible():
            self._render_profiles(intents.node_profiles)
        if intents.activation is not None:
            self._apply_engine_activation(intents.activation)
        if intents.transport is not None:
            # A transport command changes engine run state outside of an
            # activation; the session dropped its "known stopped" cache with
            # it, so the periodic refresh resumes instead of showing a stale
            # STOPPED status.
            self.statusBar().showMessage(
                trf(
                    "{verb} source {source}",
                    verb=tr(intents.transport.verb),
                    source=str(intents.transport.target)[:8],
                ),
                3000,
            )
        if intents.restart is not None:
            self._apply_engine_restart(intents.restart)

    def _on_engine_status_message(self, message: str, timeout_ms: int) -> None:
        if self._engine_closed:
            return
        intent = self.engine_status_presenter.present_status_message(message, timeout_ms)
        if intent is None:
            # "activated" and "activation_rejected" are rendered from the
            # published state.
            return
        texts = {
            "panic_ok": tr("All outputs silenced"),
            "activation_failed": tr("Engine activation failed"),
            "devices_refreshing": tr("Refreshing devices…"),
            "devices_failed": tr("Could not refresh devices"),
            "transport_failed": tr("Could not control source"),
            "panic_failed": tr("Could not send panic"),
            "restart_failed": tr("Engine restart failed"),
        }
        self.statusBar().showMessage(texts[intent.key], intent.timeout_ms)
        if intent.needs_status_refresh:
            # Every task failure changes engine run state outside of an
            # activation; the session dropped its "known stopped" cache with
            # it, so the periodic refresh resumes instead of showing a stale
            # STOPPED status.
            self._refresh_engine_status()

    @Slot(bool)
    def _on_profiler_visibility_changed(self, visible: bool) -> None:
        if not self._engine_closed:
            try:
                self.engine_bridge.set_profiling_enabled(visible)
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
        self.engine_bridge.refresh_profiles()

    def _render_profiles(self, profiles: tuple[NodeProfile, ...]) -> None:
        names = {node.node_id: node.title for node in self.session.view_model.nodes}
        if self.profiler_panel.set_profiles(profiles, names):
            self._update_profiler_heatmap(self.profiler_panel.heatmap_checkbox.isChecked())

    @Slot()
    def _reset_profiling(self) -> None:
        if self._engine_closed:
            return
        try:
            self.engine_bridge.reset_profiling()
        except (RuntimeError, TimeoutError):
            self.statusBar().showMessage(tr("Could not reset runtime profiling"), 3000)
            return
        self.scene.set_node_heatmap(None)
        self.statusBar().showMessage(tr("Runtime profiling reset"), 2000)

    @Slot(bool)
    def _update_profiler_heatmap(self, enabled: bool) -> None:
        levels = profile_heat_levels(self.profiler_panel.profiles) if enabled else None
        self.scene.set_node_heatmap(levels)

    def _show_engine_failure(self, status: EngineStatus, intent: FailureIntent) -> None:
        state = status.connection_state.value
        exit_text = "" if status.exit_code is None else f" · exit {status.exit_code}"
        log_text = (
            tr(" · no crash report file")
            if not intent.crash_report_available
            else trf(" · crash report {path}", path=status.crash_log_path)
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
        if not intent.dialog:
            # The same failure was already announced: keep the label and
            # status-bar text current, do not re-show the dialog.
            return
        message = trf(
            "The engine is {state}. The graph document remains open and editable.\n\n"
            "Details: {detail}",
            state=tr(state.lower()),
            detail=detail,
        )
        if status.exit_code is not None:
            message += trf("\nExit code: {code}", code=status.exit_code)
        if intent.crash_report_available:
            message += trf("\nCrash report: {path}", path=status.crash_log_path)
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
        metrics, profiles = self.engine_bridge.read_diagnostics()
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

    def _snapshot_default_shortcuts(self) -> None:
        """Capture each action's built-in shortcut before user overrides apply."""

        for key, source_text, _status_tip in self.action_registry.sources():
            default = self.action_registry.require(key).shortcut().toString()
            if not default:
                continue
            self._shortcut_defaults[key] = default
            self._shortcut_labels[key] = tr(source_text).replace("&", "")

    def _shortcut_items(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (key, self._shortcut_labels[key], default)
            for key, default in self._shortcut_defaults.items()
        )

    def _apply_shortcuts(self) -> None:
        """Apply configured hotkeys to every registered action.

        A configured shortcut wins over the built-in default; a key mapped to
        an empty string clears the shortcut entirely.
        """

        custom = self.preferences.shortcuts
        for key, default in self._shortcut_defaults.items():
            sequence = custom.get(key, default)
            if sequence:
                self.action_registry.require(key).setShortcut(QKeySequence(sequence))
            else:
                self.action_registry.require(key).setShortcut(QKeySequence())

    @Slot()
    def edit_preferences(self) -> None:
        dialog = PreferencesDialog(self.preferences, self, shortcut_items=self._shortcut_items())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_preferences(dialog.preferences())

    def apply_preferences(self, preferences: EditorPreferences) -> None:
        language_changed = preferences.language is not self.preferences.language
        self.preferences = preferences
        self.settings_store.save_preferences(preferences)
        self._apply_shortcuts()
        if language_changed:
            set_language(preferences.language)
        self._autosave_timer.setInterval(preferences.autosave_delay_seconds * 1000)
        self.scene.configure_grid_snap(
            enabled=preferences.grid_snap_enabled,
            spacing=preferences.grid_size,
        )
        self.document_lifecycle.truncate_recent_to(preferences.recent_file_limit)
        if language_changed:
            self._retranslate_ui()
        else:
            self.document_lifecycle.refresh_recent_menu()

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
        # Titles and descriptions live in the projected view model; rebuild
        # it before the scene sync so the cached projection is not stale.
        self.session.rebuild_view_model()
        self.scene.sync_from_session()
        self.inspector.retranslate()
        self.issues_panel.set_report(self.session.report)
        self.image_preview_panel.retranslate()
        self.note_preview_panel.retranslate()
        self.profiler_panel.retranslate()
        self._engine_status.setAccessibleName(tr("Engine runtime status"))
        self.statusBar().showMessage(tr("Ready"))
        self.document_lifecycle.refresh_recent_menu()
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

    @Slot()
    def export_midi(self) -> None:
        """Simulate the video-only graph end to end and save a Standard MIDI File."""

        if self._midi_export_job is not None:
            return
        snapshot = self.session.document.snapshot()
        eligible, _ = midi_export_eligibility(
            snapshot, self.registry, graph_valid=not self.session.report.errors
        )
        if not eligible:
            return
        stem = self.session.current_path.stem if self.session.current_path is not None else "export"
        default_path = str(Path(stem).with_suffix("")) + ".mid"
        default_path = str(
            (
                self.session.current_path.parent
                if self.session.current_path is not None
                else Path.home()
            )
            / default_path
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr("Export MIDI…"),
            default_path,
            tr("Standard MIDI File (*.mid)"),
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".mid"):
            file_path += ".mid"
        job = MidiExportJob(self, snapshot, file_path)
        self._midi_export_job = job
        job.signals.progress.connect(self._on_midi_export_progress)
        job.signals.completed.connect(self._on_midi_export_completed)
        job.signals.failed.connect(self._on_midi_export_failed)
        job.start(snapshot)

    @Slot(int, int)
    def _on_midi_export_progress(self, processed: int, total: int) -> None:
        job = self._midi_export_job
        if job is not None:
            job.overlay.set_progress(processed, total)

    @Slot(str)
    def _on_midi_export_completed(self, file_path: str) -> None:
        job = self._midi_export_job
        # Ignore results from a superseded export: its worker can outlive
        # close() because the engine shutdown phases are bounded but longer
        # than the 5-second join timeout, and a late signal must not tear
        # down a newer export's overlay.
        if job is None or self.sender() is not job.signals:
            return
        self._midi_export_job = None
        job.close()
        self.statusBar().showMessage(trf("MIDI exported: {path}", path=file_path), 8000)

    @Slot(str, str)
    def _on_midi_export_failed(self, code: str, detail: str) -> None:
        job = self._midi_export_job
        if job is None or self.sender() is not job.signals:
            return
        self._midi_export_job = None
        job.close()
        self.statusBar().showMessage(midi_export_failure_text(code, detail), 8000)

    # -- DocumentLifecycleHost ---------------------------------------------
    # MainWindow implements the lifecycle host protocol: it supplies the
    # dialogs, the recent menu, status messages, and the autosave trigger, so
    # every lifecycle behaviour is testable offscreen against a scripted host.

    @property
    def lifecycle_dialog_parent(self) -> QWidget:
        return self

    def lifecycle_status(self, text: str, timeout_ms: int) -> None:
        self.statusBar().showMessage(text, timeout_ms)

    def lifecycle_error(self, title: str, detail: str) -> None:
        self._show_error(title, detail)

    def choose_lifecycle_file(self, mode: Literal["open", "save"], initial: str) -> str:
        if mode == "open":
            selected, _ = QFileDialog.getOpenFileName(
                self, tr("Open Graph"), initial, tr(GRAPH_FILE_FILTER)
            )
            return selected
        selected, _ = QFileDialog.getSaveFileName(
            self, tr("Save Graph"), initial, tr(GRAPH_FILE_FILTER)
        )
        return selected

    def recent_menu_target(self) -> QMenu:
        return self.recent_menu

    def confirm_unsaved_changes(self) -> QMessageBox.StandardButton:
        return QMessageBox.warning(
            self,
            tr("Unsaved graph"),
            tr("Save changes before continuing?"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    def confirm_recovery(
        self, record: RecoveryRecord, explicit_path: Path | None
    ) -> QMessageBox.StandardButton:
        return QMessageBox.warning(
            self,
            tr("Recover autosaved graph"),
            trf(
                "A newer autosave was found. Restore it now?\n\nRecovery: {name}",
                name=record.path.name,
            )
            + (trf("\nOriginal: {path}", path=explicit_path) if explicit_path is not None else ""),
            QMessageBox.StandardButton.Open
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Open,
        )

    def autosave_now(self) -> None:
        self.document_lifecycle.autosave_now()

    def closeEvent(self, event: QCloseEvent) -> None:
        decision = self.document_lifecycle.confirm_replacement()
        if decision is not ReplacementDecision.CANCEL:
            if decision is ReplacementDecision.DISCARD:
                self.document_lifecycle.discard_recovery(self.session.document.document_id)
            job = self._midi_export_job
            if job is not None:
                # Stop the export worker before the shell goes down; the join is
                # bounded so a stuck export cannot block shutdown.
                self._midi_export_job = None
                job.cancel()
                job.close()
            self._activation_timer.stop()
            self._autosave_timer.stop()
            self._preview_timer.stop()
            self._metrics_timer.stop()
            self._profiler_timer.stop()
            self._device_refresh_timer.stop()
            if not self._engine_closed:
                self._engine_closed = True
                # Drain the engine task pool first: in-flight operations still
                # talk to the client, and the bridge closes it afterwards.
                self._engine_task_runner.close()
                with suppress(RuntimeError, TimeoutError):
                    self.engine_bridge.close()
            self.autosave_controller.close()
            self._save_window_state()
            event.accept()
            return
        event.ignore()
