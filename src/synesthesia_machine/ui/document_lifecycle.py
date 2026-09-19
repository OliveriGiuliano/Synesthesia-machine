"""Document lifecycle: replacement decisions, open/save, recovery, recent files.

``MainWindow`` is the Qt shell; this module owns the lifecycle behaviour:
whether a new graph may replace the current one (the unsaved-changes
decision flow), how open/save interact with the persistence facade and the
autosave recovery records, when a recovery offer is shown, and the recent
file list. The shell supplies dialogs, the recent menu, status messages,
and the autosave trigger through :class:`DocumentLifecycleHost`, so every
behaviour here is testable offscreen against a scripted host without a
window.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable
from uuid import UUID

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox, QWidget

from synesthesia_machine.persistence import (
    GraphPersistenceError,
    find_missing_media,
)
from synesthesia_machine.persistence.autosave import AutosaveStore, RecoveryRecord
from synesthesia_machine.ui.application_settings import ApplicationSettingsStore
from synesthesia_machine.ui.autosave_controller import AutosaveController
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.translations import tr, trf

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot


class ReplacementDecision(Enum):
    """Outcome of the unsaved-changes decision before a document is replaced."""

    PROCEED = auto()
    DISCARD = auto()
    CANCEL = auto()


@runtime_checkable
class DocumentLifecycleHost(Protocol):
    """What the lifecycle controller asks of the shell that hosts it.

    Dialog-returning members return the chosen standard button; the
    offscreen test host scripts them, the window shows real dialogs.
    """

    @property
    def lifecycle_dialog_parent(self) -> QWidget:
        """Widget parented dialogs are shown from."""
        ...

    def lifecycle_status(self, text: str, timeout_ms: int) -> None:
        """Transient status-bar message."""
        ...

    def lifecycle_error(self, title: str, detail: str) -> None:
        """Blocking error dialog plus a status message."""
        ...

    def choose_lifecycle_file(self, mode: Literal["open", "save"], initial: str) -> str:
        """Open/save file dialog; empty string when the user cancels."""
        ...

    def recent_menu_target(self) -> QMenu:
        """The menu that lists recently opened graphs."""
        ...

    def confirm_unsaved_changes(self) -> QMessageBox.StandardButton:
        """Save/Discard/Cancel dialog shown when a replacement finds unsaved work."""
        ...

    def confirm_recovery(
        self, record: RecoveryRecord, explicit_path: Path | None
    ) -> QMessageBox.StandardButton:
        """Open/Discard/Cancel dialog for one autosave recovery candidate."""
        ...

    def autosave_now(self) -> None:
        """Trigger an autosave of the current document (no-op when clean)."""
        ...


class DocumentLifecycleController:
    """Decisions and effects of the document lifecycle, off any window.

    The controller is the single owner of the recent-file list and of the
    replacement decision flow: open, new, generated-graph replacement, and
    close all ask it, so the window (and its action states) cannot drift from
    the behaviour a user actually experiences.
    """

    def __init__(
        self,
        *,
        host: DocumentLifecycleHost,
        session: DocumentSession,
        settings_store: ApplicationSettingsStore,
        autosave_store: AutosaveStore,
        autosave_controller: AutosaveController,
        recent_file_limit: Callable[[], int],
    ) -> None:
        self._host = host
        self._session = session
        self._settings_store = settings_store
        self._autosave_store = autosave_store
        self._autosave_controller = autosave_controller
        self._recent_file_limit = recent_file_limit
        self._recent_paths = list(self._settings_store.load_recent_files(self._recent_file_limit()))

    @property
    def recent_paths(self) -> tuple[Path, ...]:
        return tuple(self._recent_paths)

    # -- replacement decision flow -----------------------------------------

    def confirm_replacement(self) -> ReplacementDecision:
        """Decide whether a new graph may replace the current one.

        A clean document proceeds without asking. A dirty document gets an
        autosave attempt first, then the Save/Discard/Cancel question:
        saving fails, the replacement is cancelled.
        """

        if not self._session.is_dirty:
            return ReplacementDecision.PROCEED
        self._host.autosave_now()
        choice = self._host.confirm_unsaved_changes()
        if choice == QMessageBox.StandardButton.Save:
            return ReplacementDecision.PROCEED if self.save() else ReplacementDecision.CANCEL
        if choice == QMessageBox.StandardButton.Discard:
            return ReplacementDecision.DISCARD
        return ReplacementDecision.CANCEL

    def new_document(self) -> None:
        previous_id = self._session.document.document_id
        decision = self.confirm_replacement()
        if decision is ReplacementDecision.CANCEL:
            return
        self._session.new_document()
        if decision is ReplacementDecision.DISCARD:
            self.discard_recovery(previous_id)
        self._host.lifecycle_status(tr("Created new graph"), 3000)

    def replace_with_generated(self, builder: Callable[[], GraphSnapshot]) -> bool:
        """Replace the document with a freshly built graph through the
        replacement decision flow.

        The builder runs only after the user consents, so a cancelled
        decision performs no generation work; a builder failure reports
        through the host and leaves the document untouched. Returns True when
        the document was replaced.
        """
        previous_id = self._session.document.document_id
        decision = self.confirm_replacement()
        if decision is ReplacementDecision.CANCEL:
            return False
        try:
            snapshot = builder()
        except (KeyError, RuntimeError, ValueError) as error:
            self._host.lifecycle_error("Could not generate random graph", str(error))
            return False
        self._session.replace_with_snapshot(snapshot)
        if decision is ReplacementDecision.DISCARD:
            self.discard_recovery(previous_id)
        return True

    def confirm_close(self) -> bool:
        """Run the replacement decision flow for an application close.

        Nothing is replaced - the app is going down - so a DISCARD answer
        only drops the current document's recovery record. True when the
        close may proceed.
        """
        decision = self.confirm_replacement()
        if decision is ReplacementDecision.CANCEL:
            return False
        if decision is ReplacementDecision.DISCARD:
            self.discard_recovery(self._session.document.document_id)
        return True

    # -- open / save --------------------------------------------------------

    def open_interactive(self) -> None:
        chosen = self._host.choose_lifecycle_file("open", "")
        if chosen:
            self.open_path(Path(chosen))

    def open_path(self, path: Path) -> bool:
        previous_id = self._session.document.document_id
        decision = self.confirm_replacement()
        if decision is ReplacementDecision.CANCEL:
            return False
        try:
            self._session.open_document(path)
        except (GraphPersistenceError, OSError, ValueError) as error:
            self._host.lifecycle_error("Could not open graph", str(error))
            return False
        if decision is ReplacementDecision.DISCARD:
            self.discard_recovery(previous_id)
        self.remember(path)
        missing_count = len(find_missing_media(self._session.document.snapshot()))
        if missing_count:
            self._host.lifecycle_status(
                trf(
                    "Opened {name} · {count} missing media file(s); "
                    "use File → Locate Missing Media",
                    name=path.name,
                    count=missing_count,
                ),
                8000,
            )
        else:
            self._host.lifecycle_status(trf("Opened {name}", name=path.name), 4000)
        return True

    def save(self) -> bool:
        if self._session.current_path is None:
            return self.save_as()
        return self.save_to_path(self._session.current_path)

    def save_as(self) -> bool:
        initial = str(
            self._session.current_path or Path.home() / f"{tr('Untitled')}.synmachine.json"
        )
        chosen = self._host.choose_lifecycle_file("save", initial)
        if not chosen:
            return False
        path = Path(chosen)
        if not path.suffix:
            path = path.with_suffix(".synmachine.json")
        return self.save_to_path(path)

    def save_to_path(self, path: Path) -> bool:
        document_id = self._session.document.document_id
        try:
            saved = self._session.save(path)
        except (OSError, ValueError) as error:
            self._host.lifecycle_error("Could not save graph", str(error))
            return False
        self.discard_recovery(document_id)
        self.remember(saved)
        self._host.lifecycle_status(trf("Saved {name}", name=saved.name), 4000)
        return True

    # -- autosave and recovery ----------------------------------------------

    def autosave_now(self) -> None:
        if not self._session.is_dirty:
            return
        self._autosave_controller.request(
            self._session.document.snapshot(),
            explicit_path=self._session.current_path,
        )

    def discard_recovery(self, document_id: UUID) -> None:
        self._autosave_controller.discard(document_id)

    def offer_recovery(self) -> None:
        """Walk the autosave candidates, oldest last; one choice per record.

        ``Cancel`` stops the offer loop, ``Discard`` drops that one record
        and continues, ``Open`` restores and stops.
        """

        for record in self._autosave_store.discover():
            explicit_path = self._autosave_store.explicit_path_for(record, self._recent_paths)
            if not record.is_newer_than(explicit_path):
                continue
            choice = self._host.confirm_recovery(record, explicit_path)
            if choice == QMessageBox.StandardButton.Discard:
                self.discard_recovery(record.document_id)
                continue
            if choice != QMessageBox.StandardButton.Open:
                return
            try:
                self._session.recover_document(record.path, explicit_path=explicit_path)
            except (GraphPersistenceError, OSError, ValueError) as error:
                self._host.lifecycle_error("Could not recover graph", str(error))
                return
            self._host.lifecycle_status(trf("Recovered {name}", name=record.path.name), 5000)
            return

    # -- recent files ---------------------------------------------------------

    def remember(self, path: Path) -> None:
        self._recent_paths = self._settings_store.remember_recent(
            path,
            self._recent_paths,
            self._recent_file_limit(),
        )
        self.refresh_recent_menu()

    def clear_recent(self) -> None:
        self._recent_paths = []
        self._settings_store.clear_recent()
        self.refresh_recent_menu()

    def truncate_recent_to(self, limit: int) -> None:
        """Shrink the list after a lower recent-file limit; persist the result."""

        self._recent_paths = self._recent_paths[:limit]
        self._settings_store.truncate_recent_files(limit)

    def refresh_recent_menu(self) -> None:
        menu = self._host.recent_menu_target()
        menu.clear()
        if not self._recent_paths:
            empty = QAction(tr("No recent graphs"), menu)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for index, path in enumerate(self._recent_paths, start=1):
            action = QAction(f"&{index} {path.name}", menu)
            action.setToolTip(str(path))
            action.triggered.connect(partial(self.open_path, path))
            menu.addAction(action)
        menu.addSeparator()
        clear_action = QAction(tr("&Clear Recent Graphs"), menu)
        clear_action.triggered.connect(self.clear_recent)
        menu.addAction(clear_action)
