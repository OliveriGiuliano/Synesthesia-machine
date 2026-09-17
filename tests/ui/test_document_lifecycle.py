"""Offscreen tests for the document lifecycle controller.

The controller is the deep module that owns the replacement decision flow,
open/save, autosave recovery, and the recent-file list. These tests drive it
through a scripted host so its behaviour is pinned without a window or any
dialog, which is exactly the seam the extraction created.
"""

from __future__ import annotations

from concurrent.futures import Executor, Future
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence.autosave import AutosaveStore
from synesthesia_machine.ui.application_settings import ApplicationSettingsStore
from synesthesia_machine.ui.autosave_controller import AutosaveController
from synesthesia_machine.ui.document_lifecycle import (
    DocumentLifecycleController,
    ReplacementDecision,
)
from synesthesia_machine.ui.session import DocumentSession


class _InlineExecutor(Executor):
    """concurrent.futures.Executor that runs submit() on the calling thread.

    Injected into the autosave controller in tests so recovery discards and
    saves settle before the call that queued them returns; assertions on the
    store then observe settled state instead of racing a worker thread.
    """

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


class ScriptedHost:
    """A scripted DocumentLifecycleHost: no dialogs, every effect recorded."""

    def __init__(self, dialog_parent: QWidget, recent_menu: QMenu) -> None:
        self.dialog_parent = dialog_parent
        self.recent_menu = recent_menu
        self.unsaved_choice = QMessageBox.StandardButton.Cancel
        self.recovery_choice = QMessageBox.StandardButton.Cancel
        self.chosen_file = ""
        self.statuses: list[tuple[str, int]] = []
        self.errors: list[tuple[str, str]] = []
        self.autosave_requests = 0

    @property
    def lifecycle_dialog_parent(self) -> QWidget:
        return self.dialog_parent

    def lifecycle_status(self, text: str, timeout_ms: int) -> None:
        self.statuses.append((text, timeout_ms))

    def lifecycle_error(self, title: str, detail: str) -> None:
        self.errors.append((title, detail))

    def choose_lifecycle_file(self, mode: str, initial: str) -> str:
        del mode, initial
        return self.chosen_file

    def recent_menu_target(self) -> QMenu:
        return self.recent_menu

    def confirm_unsaved_changes(self) -> QMessageBox.StandardButton:
        return self.unsaved_choice

    def confirm_recovery(
        self, record: object, explicit_path: Path | None
    ) -> QMessageBox.StandardButton:
        del record, explicit_path
        return self.recovery_choice

    def autosave_now(self) -> None:
        self.autosave_requests += 1


class LifecycleEnv:
    """The controller plus the dependencies the tests need to reach directly."""

    def __init__(self, host: ScriptedHost, tmp_path: Path) -> None:
        self.host = host
        self.settings_store = ApplicationSettingsStore(
            QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        )
        self.autosave_store = AutosaveStore(tmp_path / "data" / "recovery")
        self.session = DocumentSession(create_utility_registry())
        self.autosave_controller = AutosaveController(
            self.autosave_store, session=self.session, executor=_InlineExecutor()
        )
        self.controller = DocumentLifecycleController(
            host=host,
            session=self.session,
            settings_store=self.settings_store,
            autosave_store=self.autosave_store,
            autosave_controller=self.autosave_controller,
            recent_file_limit=lambda: 5,
        )


@pytest.fixture
def env(qapp: QApplication, tmp_path: Path) -> LifecycleEnv:
    del qapp
    return LifecycleEnv(ScriptedHost(QWidget(), QMenu()), tmp_path)


def _dirty(env: LifecycleEnv) -> None:
    env.session.add_node("synmachine.utility.number", (0.0, 0.0))


def _write_recovery(env: LifecycleEnv, node_x: float) -> Path:
    document = GraphDocument()
    document.add_node("synmachine.utility.number", position=(node_x, 20.0))
    return env.autosave_store.save(document.snapshot())


def test_clean_document_proceeds_without_asking(env: LifecycleEnv) -> None:
    assert env.controller.confirm_replacement() is ReplacementDecision.PROCEED
    assert env.host.autosave_requests == 0
    assert env.host.statuses == []


def test_dirty_document_save_success_proceeds(env: LifecycleEnv, tmp_path: Path) -> None:
    _dirty(env)
    env.session.current_path = tmp_path / "current.synmachine.json"
    env.host.unsaved_choice = QMessageBox.StandardButton.Save

    assert env.controller.confirm_replacement() is ReplacementDecision.PROCEED
    assert env.host.autosave_requests == 1
    assert (tmp_path / "current.synmachine.json").exists()


def test_dirty_document_save_failure_cancels(env: LifecycleEnv, tmp_path: Path) -> None:
    _dirty(env)
    # A directory path makes the save raise an OSError, which the controller
    # surfaces as a lifecycle error and turns into a cancellation.
    env.session.current_path = tmp_path
    env.host.unsaved_choice = QMessageBox.StandardButton.Save

    assert env.controller.confirm_replacement() is ReplacementDecision.CANCEL
    assert env.host.errors and env.host.errors[0][0] == "Could not save graph"


def test_dirty_document_discard_choice(env: LifecycleEnv) -> None:
    _dirty(env)
    env.host.unsaved_choice = QMessageBox.StandardButton.Discard
    assert env.controller.confirm_replacement() is ReplacementDecision.DISCARD


def test_dirty_document_cancel_choice(env: LifecycleEnv) -> None:
    _dirty(env)
    env.host.unsaved_choice = QMessageBox.StandardButton.Cancel
    assert env.controller.confirm_replacement() is ReplacementDecision.CANCEL


def test_new_document_cancel_leaves_document_unchanged(env: LifecycleEnv) -> None:
    before = env.session.document.document_id
    _dirty(env)
    env.host.unsaved_choice = QMessageBox.StandardButton.Cancel
    env.controller.new_document()
    assert env.session.document.document_id == before
    assert env.host.statuses == []


def test_new_document_proceed_on_clean_document(env: LifecycleEnv) -> None:
    before = env.session.document.document_id
    env.controller.new_document()
    assert env.session.document.document_id != before
    assert env.host.statuses and env.host.statuses[0][0].startswith("Created")


def test_remember_and_truncate_recent_list(env: LifecycleEnv, tmp_path: Path) -> None:
    first = tmp_path / "one.synmachine.json"
    second = tmp_path / "two.synmachine.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")

    env.controller.remember(first)
    env.controller.remember(second)
    assert env.controller.recent_paths == (second, first)

    # A lowered limit caps the persisted list; the newest entry survives.
    env.controller.truncate_recent_to(1)
    assert env.controller.recent_paths == (second,)


def test_clear_recent_empties_list_and_menu(env: LifecycleEnv, tmp_path: Path) -> None:
    first = tmp_path / "one.synmachine.json"
    first.write_text("{}", encoding="utf-8")
    env.controller.remember(first)
    assert len(env.host.recent_menu.actions()) > 0

    env.controller.clear_recent()
    assert env.controller.recent_paths == ()
    # The single "No recent graphs" placeholder occupies the empty menu.
    assert [action.text() for action in env.host.recent_menu.actions()] == ["No recent graphs"]


def test_offer_recovery_discard_continues_through_all_records(env: LifecycleEnv) -> None:
    _write_recovery(env, 10.0)
    _write_recovery(env, 30.0)
    env.host.recovery_choice = QMessageBox.StandardButton.Discard

    env.controller.offer_recovery()

    # Every candidate was offered and discarded; the document is untouched.
    assert env.autosave_store.discover() == ()
    assert env.session.document.nodes == ()


def test_offer_recovery_cancel_stops_immediately(env: LifecycleEnv) -> None:
    first = _write_recovery(env, 10.0)
    second = _write_recovery(env, 30.0)
    env.host.recovery_choice = QMessageBox.StandardButton.Cancel

    env.controller.offer_recovery()

    # Cancel stops the loop at the first candidate: nothing is discarded and
    # nothing is recovered.
    assert {record.path for record in env.autosave_store.discover()} == {first, second}
    assert env.session.document.nodes == ()
    assert env.host.statuses == []
