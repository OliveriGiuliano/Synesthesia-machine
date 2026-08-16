"""Phase 7 batch 3 typed application settings, recent files, and grid snapping."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.runtime import InProcessEngineClient
from synesthesia_machine.ui.application_settings import (
    ApplicationSettingsStore,
    EditorPreferences,
    PreferencesDialog,
)
from synesthesia_machine.ui.canvas import GraphScene
from synesthesia_machine.ui.main_window import MainWindow
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME
from synesthesia_machine.ui.translations import UiLanguage


def _settings(path: Path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_preferences_round_trip_and_invalid_values_fall_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.ini"
    store = ApplicationSettingsStore(_settings(path))
    assert store.load_preferences() == EditorPreferences()

    expected = EditorPreferences(
        autosave_delay_seconds=90,
        grid_snap_enabled=True,
        grid_size=32.0,
        recent_file_limit=12,
    )
    store.save_preferences(expected)
    assert ApplicationSettingsStore(_settings(path)).load_preferences() == expected

    broken = _settings(path)
    broken.setValue("editor/autosaveDelaySeconds", "soon")
    broken.setValue("editor/gridSnapEnabled", "maybe")
    broken.setValue("editor/gridSize", float("nan"))
    broken.setValue("editor/recentFileLimit", 200)
    broken.setValue("editor/language", "de")
    broken.sync()
    assert ApplicationSettingsStore(_settings(path)).load_preferences() == EditorPreferences()


def test_recent_files_are_resolved_deduplicated_pruned_and_limited(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "recent.ini")
    store = ApplicationSettingsStore(settings)
    first = tmp_path / "first.synmachine.json"
    second = tmp_path / "second.synmachine.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    missing = tmp_path / "missing.synmachine.json"
    settings.setValue(
        "recentFiles",
        [str(first), str(missing), str(first), str(second)],
    )

    assert store.load_recent_files(2) == [first.resolve(), second.resolve()]
    remembered = store.remember_recent(second, [first.resolve(), second.resolve()], 2)
    assert remembered == [second.resolve(), first.resolve()]
    assert ApplicationSettingsStore(_settings(tmp_path / "recent.ini")).load_recent_files(2) == (
        remembered
    )

    store.clear_recent()
    assert store.load_recent_files(2) == []


def test_grid_snap_commits_as_one_exact_undoable_move(qapp: QApplication) -> None:
    del qapp
    session = DocumentSession(create_utility_registry())
    node_id = session.add_node("synmachine.utility.number", (0.0, 0.0))
    scene = GraphScene(session, DEFAULT_THEME)
    scene.configure_grid_snap(enabled=True, spacing=20.0)
    scene.select_node_ids({node_id})
    item = scene.node_items[node_id]
    origins = scene.selected_node_positions()
    item.setPos(13.0, 37.0)
    scene.commit_node_move(origins)

    node = session.document.node(node_id)
    assert node is not None and node.position == (20.0, 40.0)
    session.undo_stack.undo()
    node = session.document.node(node_id)
    assert node is not None and node.position == (0.0, 0.0)


def test_main_window_applies_persisted_preferences_and_exposes_action(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    settings = _settings(tmp_path / "window.ini")
    store = ApplicationSettingsStore(settings)
    preferences = EditorPreferences(75, True, 30.0, 5)
    store.save_preferences(preferences)
    paths = ApplicationPaths(tmp_path / "data", tmp_path / "logs", tmp_path / "recovery")
    registry = create_utility_registry()
    window = MainWindow(
        registry,
        paths,
        InProcessEngineClient(registry),
        settings=settings,
        offer_recovery=False,
    )
    try:
        assert window.preferences == preferences
        assert window._autosave_timer.interval() == 75_000
        assert window.scene.grid_snap_enabled
        assert window.scene.grid_spacing == 30.0
        assert window.action_registry.require("preferences").isEnabled()
    finally:
        window.session.new_document()
        window.close()


def test_french_preference_translates_startup_and_can_switch_live(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    settings = _settings(tmp_path / "french.ini")
    store = ApplicationSettingsStore(settings)
    french = EditorPreferences(language=UiLanguage.FRENCH)
    store.save_preferences(french)
    assert ApplicationSettingsStore(_settings(tmp_path / "french.ini")).load_preferences() == french

    paths = ApplicationPaths(tmp_path / "data", tmp_path / "logs", tmp_path / "recovery")
    registry = create_utility_registry()
    window = MainWindow(
        registry,
        paths,
        InProcessEngineClient(registry),
        settings=settings,
        offer_recovery=False,
    )
    try:
        menus = tuple(action.text().replace("&", "") for action in window.menuBar().actions())
        assert menus == ("Fichier", "Édition", "Affichage", "Graphe", "MIDI", "Aide")
        assert window.library_dock.windowTitle() == "Bibliothèque de nœuds"
        assert window.library.search.placeholderText() == "Rechercher des nœuds…"
        assert window.inspector.title.text() == "Aucune sélection"
        assert window.action_registry.require("open").text().replace("&", "") == "Ouvrir…"
        number_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
        assert window.scene.node_items[number_id].view_model.title == "Nombre"
        standard_buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        assert (
            standard_buttons.button(QDialogButtonBox.StandardButton.Save).text().replace("&", "")
            == "Enregistrer"
        )
        assert (
            standard_buttons.button(QDialogButtonBox.StandardButton.Cancel).text().replace("&", "")
            == "Annuler"
        )

        dialog = PreferencesDialog(window.preferences, window)
        assert dialog.windowTitle() == "Préférences"
        assert dialog.language_combo.currentData() == UiLanguage.FRENCH.value
        dialog.close()

        window.apply_preferences(EditorPreferences(language=UiLanguage.ENGLISH))
        menus = tuple(action.text().replace("&", "") for action in window.menuBar().actions())
        assert menus == ("File", "Edit", "View", "Graph", "MIDI", "Help")
        assert window.library_dock.windowTitle() == "Node Library"
        assert window.inspector.title.text() == "Nothing selected"
        assert window.scene.node_items[number_id].view_model.title == "Number"
    finally:
        window.session.new_document()
        window.close()
