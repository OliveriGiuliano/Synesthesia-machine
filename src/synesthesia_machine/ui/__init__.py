"""PySide6 user-interface adapters for the Qt-free graph domain.

The public surface of the editor: the document session, the engine bridge,
the preview router, the autosave controller, the action registry, the graph
view models, and the translation helpers. Compose the application shell from
these; everything else in the package is internal wiring.
"""

from synesthesia_machine.ui.actions import ActionRegistry, ActionSpec
from synesthesia_machine.ui.application_settings import (
    ApplicationSettingsStore,
    EditorPreferences,
    PreferencesDialog,
)
from synesthesia_machine.ui.autosave_controller import AutosaveController
from synesthesia_machine.ui.engine_bridge import (
    EngineBridge,
    EngineBridgeState,
    EngineRestartOutcome,
)
from synesthesia_machine.ui.preview_router import PreviewRouter, PumpedPreviews
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.translations import tr, trf
from synesthesia_machine.ui.view_models import (
    ConnectionViewModel,
    GraphViewModel,
    NodeViewModel,
    ParameterGroupViewModel,
    ParameterViewModel,
    PortViewModel,
    project_graph,
)

__all__ = [
    "ActionRegistry",
    "ActionSpec",
    "ApplicationSettingsStore",
    "AutosaveController",
    "ConnectionViewModel",
    "DocumentSession",
    "EditorPreferences",
    "EngineBridge",
    "EngineBridgeState",
    "EngineRestartOutcome",
    "GraphViewModel",
    "NodeViewModel",
    "ParameterGroupViewModel",
    "ParameterViewModel",
    "PortViewModel",
    "PreferencesDialog",
    "PreviewRouter",
    "PumpedPreviews",
    "project_graph",
    "tr",
    "trf",
]
