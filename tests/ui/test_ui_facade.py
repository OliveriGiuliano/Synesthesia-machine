"""Tests that the ui package facade publishes its documented surface.

The facade is the reviewable front door of the editor package; a missing or
misnamed export would surface here instead of at a call site.
"""

from __future__ import annotations

import importlib

import synesthesia_machine.ui as ui


def test_facade_exports_resolve_to_their_implementation_objects() -> None:
    for name in ui.__all__:
        exported = getattr(ui, name)
        assert exported is not None, f"ui.{name} is listed in __all__ but not importable"
        # Spot-check that the headline adapters are re-exports, not re-wrappers.
        source = {
            "DocumentSession": "synesthesia_machine.ui.session",
            "EngineBridge": "synesthesia_machine.ui.engine_bridge",
            "PreviewRouter": "synesthesia_machine.ui.preview_router",
            "AutosaveController": "synesthesia_machine.ui.autosave_controller",
            "GraphViewModel": "synesthesia_machine.ui.view_models",
            "project_graph": "synesthesia_machine.ui.view_models",
            "tr": "synesthesia_machine.ui.translations",
        }
        if name in source:
            owner = importlib.import_module(source[name])
            assert exported is getattr(owner, name)


def test_facade_lists_every_documented_adapter() -> None:
    expected = {
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
        "PreferencesDialog",
        "PreviewRouter",
        "PumpedPreviews",
        "PortViewModel",
        "project_graph",
        "tr",
        "trf",
    }
    assert expected <= set(ui.__all__)
