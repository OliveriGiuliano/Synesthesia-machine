"""Typed application settings, recent files, and grid snapping tests."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QKeySequenceEdit

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


def test_shortcut_preferences_round_trip_and_invalid_values_fall_back(tmp_path: Path) -> None:
    path = tmp_path / "shortcuts.ini"
    store = ApplicationSettingsStore(_settings(path))
    expected = EditorPreferences(shortcuts={"play": "Ctrl+K", "pause": ""})
    store.save_preferences(expected)
    assert ApplicationSettingsStore(_settings(path)).load_preferences() == expected

    broken = _settings(path)
    broken.setValue("editor/shortcuts", "not-a-mapping")
    broken.sync()
    reloaded = ApplicationSettingsStore(_settings(path)).load_preferences()
    assert reloaded.shortcuts == {}
    assert reloaded.autosave_delay_seconds == expected.autosave_delay_seconds


def test_preferences_dialog_edits_shortcut_sequences(qapp: QApplication) -> None:
    del qapp
    dialog = PreferencesDialog(
        EditorPreferences(shortcuts={"play": "Ctrl+K"}),
        None,
        shortcut_items=(
            ("play", "Play", "F5"),
            ("pause", "Pause", "F6"),
        ),
    )

    play_edit = dialog.findChild(QKeySequenceEdit, "shortcut_play")
    pause_edit = dialog.findChild(QKeySequenceEdit, "shortcut_pause")
    assert play_edit is not None and pause_edit is not None
    # A configured override is shown; an unconfigured action keeps its default.
    assert play_edit.keySequence() == QKeySequence("Ctrl+K")
    assert pause_edit.keySequence() == QKeySequence("F6")

    play_edit.setKeySequence(QKeySequence("Ctrl+Shift+K"))
    pause_edit.setKeySequence(QKeySequence())
    shortcuts = dialog.preferences().shortcuts
    assert shortcuts["play"] == "Ctrl+Shift+K"
    assert shortcuts["pause"] == ""


def test_main_window_applies_and_reapplies_configured_shortcuts(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    settings = _settings(tmp_path / "shortcuts.ini")
    settings.setValue("editor/shortcuts", {"play": "Ctrl+K"})
    settings.sync()
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
        assert window.action_registry.require("play").shortcut() == QKeySequence("Ctrl+K")
        # An empty configured shortcut clears the action's default binding.
        window.apply_preferences(EditorPreferences(shortcuts={"pause": ""}))
        assert window.action_registry.require("pause").shortcut().toString() == ""
        # Falling back to defaults restores the built-in bindings.
        window.apply_preferences(EditorPreferences())
        assert window.action_registry.require("play").shortcut() == QKeySequence("F5")
    finally:
        window.session.new_document()
        window.close()


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


def test_recent_files_display_limit_does_not_destroy_the_persisted_list(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "recent.ini")
    store = ApplicationSettingsStore(settings)
    files = [tmp_path / f"graph_{index}.synmachine.json" for index in range(6)]
    for path in files:
        path.write_text("{}", encoding="utf-8")
    settings.setValue("recentFiles", [str(path) for path in files])

    # A small display limit caps the returned view only.
    visible = store.load_recent_files(2)
    assert visible == [files[0].resolve(), files[1].resolve()]

    # A fresh view of the INI still holds the full list: the display
    # limit must never have truncated what is persisted.
    reloaded = ApplicationSettingsStore(_settings(tmp_path / "recent.ini")).load_recent_files(20)
    assert reloaded == [path.resolve() for path in files]


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
        window.session.add_node("synmachine.utility.number", (0.0, 0.0))
        assert window.autosave_controller._own_timer.interval() == 75_000
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
        assert menus == ("Fichier", "Édition", "Affichage", "Graphe", "Sorties", "Aide")
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
        assert menus == ("File", "Edit", "View", "Graph", "Outputs", "Help")
        assert window.library_dock.windowTitle() == "Node Library"
        assert window.inspector.title.text() == "Nothing selected"
        assert window.scene.node_items[number_id].view_model.title == "Number"
    finally:
        window.session.new_document()
        window.close()
