"""Coalesced background autosave orchestration for the Qt document session."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from synesthesia_machine.graph import GraphSnapshot
from synesthesia_machine.persistence.autosave import AutosaveStore
from synesthesia_machine.ui.engine_task_runner import QtUiClock
from synesthesia_machine.ui.session import DocumentSession

if TYPE_CHECKING:
    from synesthesia_machine.ui.engine_bridge import UiClock

# How long close() waits for queued recovery work to drain. Matches the
# bounded worker-join timeouts used by the other UI background services.
_CLOSE_GRACE_PERIOD_S = 5.0


@dataclass(frozen=True, slots=True)
class _AutosaveRequest:
    snapshot: GraphSnapshot
    explicit_path: Path | None


class AutosaveController(QObject):
    """Write recovery snapshots off the UI thread and own the autosave trigger.

    The controller owns the whole autosave behaviour of the watched document:
    the debounce trigger (re-armed by every session change), the immediate
    pre-replacement save, the coalesced background writer, and the recovery
    record maintenance. Callers only push snapshots through :meth:`request` or
    ask for :meth:`autosave_now`.
    """

    saved = Signal(object)
    failed = Signal(object)
    _finished = Signal(object, object)

    def __init__(
        self,
        store: AutosaveStore,
        parent: QObject | None = None,
        session: DocumentSession | None = None,
        executor: Executor | None = None,
        clock: UiClock | None = None,
        delay_provider: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._session = session
        self._executor = (
            executor
            if executor is not None
            else ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="synmachine-ui-autosave",
            )
        )
        # The default clock owns its timer so close() can stop it; an
        # injected clock (tests, the production window) owns its own timer.
        self._own_timer: QTimer | None = None
        if clock is None:
            self._own_timer = QTimer(self)
            clock = QtUiClock(self._own_timer)
        self._clock = clock
        self._delay_provider = delay_provider
        # Stable callback reference: schedule_once and cancel compare
        # callbacks by identity, and a freshly fetched bound method would
        # never match the one the clock is holding.
        self._due = self.autosave_now
        self._future: Future[object] | None = None
        self._current: _AutosaveRequest | None = None
        self._pending: _AutosaveRequest | None = None
        # Documents for which a recovery record may exist on disk in this
        # process: every document a snapshot was requested for, until it is
        # discarded or the controller closes.
        self._tracked_documents: set[UUID] = set()
        # Explicit discard requests that arrived while a save for the same
        # document was still on the worker; they run once that save settles.
        self._pending_discards: set[UUID] = set()
        # Every task on the worker, so close() can wait for queued
        # saves/discards with a bounded timeout.
        self._outstanding: set[Future[object]] = set()
        self._closed = False
        if session is not None:
            session.dirtyChanged.connect(self._on_document_dirty_changed)
            # Every rebuild re-arms the debounce; schedule() itself is a
            # no-op for a clean document, mirroring the trigger chain the
            # window owned before it moved here.
            session.changed.connect(self.schedule)
        self._finished.connect(self._on_finished)

    def request(self, snapshot: GraphSnapshot, *, explicit_path: Path | None) -> None:
        if self._closed:
            return
        request = _AutosaveRequest(snapshot, explicit_path)
        self._tracked_documents.add(snapshot.document_id)
        if self._future is not None:
            self._pending = request
            return
        self._start(request)

    def autosave_now(self) -> None:
        """Save the watched document immediately, bypassing the debounce.

        This is both the debounce's fire callback and the entry point for the
        synchronous pre-replacement save. The snapshot and the explicit path
        come from the session, so no other object carries that knowledge; a
        clean document or a closed controller is a no-op.
        """
        if self._closed or self._session is None:
            return
        if not self._session.is_dirty:
            return
        self.request(
            self._session.document.snapshot(),
            explicit_path=self._session.current_path,
        )

    def schedule(self) -> None:
        """Re-arm the autosave debounce for the watched document.

        Every session change (edits, engine-status projections, and every
        other rebuild) funnels through here; a clean document never
        schedules. The delay is read from the provider at schedule time, so
        a preference change takes effect from the next re-arm, while an
        armed debounce keeps the delay it was armed with.
        """
        if self._closed or self._session is None or self._delay_provider is None:
            return
        if not self._session.is_dirty:
            return
        delay_s = self._delay_provider()
        if not delay_s > 0:
            return
        self._clock.schedule_once(int(delay_s * 1000), self._due)

    def discard(self, document_id: UUID) -> None:
        if self._closed:
            return
        if self._pending is not None and self._pending.snapshot.document_id == document_id:
            # The moment its document leaves the dirty state the queued
            # snapshot is stale; dropping it also keeps it from rewriting
            # the record this discard is about to remove.
            self._pending = None
        if self._autosave_active_for(document_id):
            # A save for this document is still on the worker; it would
            # rewrite the record after a discard ran. Defer the discard:
            # the finish handler re-checks it once the save settles.
            self._tracked_documents.add(document_id)
            self._pending_discards.add(document_id)
            return
        self._discard_record(document_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Cancel the pending debounce before draining: a fire during the
        # drain is a no-op (the _closed gate in autosave_now), and stopping
        # the self-owned timer keeps it from firing after the worker is
        # already shut down.
        self._clock.cancel(self._due)
        if self._own_timer is not None:
            self._own_timer.stop()
        request = self._pending
        self._pending = None
        if request is not None:
            # Only queued in the controller, not yet on the worker; submit
            # it so the newest dirty snapshot is not lost when the app
            # exits.
            self._start(request)
        protected: UUID | None = None
        if self._session is not None and self._session.is_dirty:
            # A dirty document at exit is exactly what recovery is for;
            # every other tracked document already went clean (or was
            # saved/discarded), so its record is discarded behind any save
            # still on the worker (the single worker keeps FIFO order).
            protected = self._session.document.document_id
        for document_id in tuple(self._tracked_documents):
            if document_id is not protected:
                self._discard_record(document_id)
        # Drain the queued work with a bounded wait: shutdown(wait=True)
        # would block the exit path on a stuck store task, and cancelling
        # queued discards would leave stale recovery files behind.
        self._executor.shutdown(wait=False)
        deadline = time.monotonic() + _CLOSE_GRACE_PERIOD_S
        for future in tuple(self._outstanding):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with suppress(BaseException):
                future.result(timeout=remaining)

    def _start(self, request: _AutosaveRequest) -> None:
        future = self._submit(
            self._store.save, request.snapshot, explicit_path=request.explicit_path
        )
        if future is None:
            # close() shut the worker down (possibly from another thread);
            # the task is dropped at shutdown.
            return
        self._future = future
        self._current = request
        future.add_done_callback(self._publish_result)

    def _publish_result(self, future: Future[object]) -> None:
        try:
            result: object = future.result()
        except BaseException as error:
            result = error
        with suppress(RuntimeError):
            self._finished.emit(future, result)

    @Slot(object, object)
    def _on_finished(self, future: object, result: object) -> None:
        if future is not self._future:
            return
        self._future = None
        self._current = None
        if isinstance(result, Path):
            self.saved.emit(result)
        elif isinstance(result, BaseException):
            self.failed.emit(result)
        # A clean transition or an explicit discard can arrive while this
        # save is still on the worker; now that it has settled, run the
        # discards it deferred. _pending is still visible, so a queued
        # save for the same document re-defers the discard instead of it
        # deleting a file the save is about to write.
        self._flush_settled_discards()
        request = self._pending
        self._pending = None
        if request is not None and not self._closed:
            self._start(request)

    @Slot(bool)
    def _on_document_dirty_changed(self, dirty: bool) -> None:
        if dirty:
            self.schedule()
            return
        if self._closed:
            return
        # The document went back to clean without a save (undo/redo to the
        # last saved state, or a replaced document): its recovery record
        # now matches, or is older than, what is on disk. A save still on
        # the worker is deferred: the finish handler runs the same flush
        # once it settles.
        self._clock.cancel(self._due)
        self._flush_settled_discards()

    def _autosave_active_for(self, document_id: UUID) -> bool:
        current = self._current
        if current is not None and current.snapshot.document_id == document_id:
            return True
        pending = self._pending
        return pending is not None and pending.snapshot.document_id == document_id

    def _flush_settled_discards(self) -> None:
        if self._session is not None and not self._session.is_dirty:
            # The watched document went clean, so its record (if any) is
            # stale even if its last save only just settled.
            current_id = self._session.document.document_id
            if current_id in self._tracked_documents:
                self._discard_record(current_id)
        for document_id in tuple(self._pending_discards):
            if self._autosave_active_for(document_id):
                continue
            self._discard_record(document_id)

    def _discard_record(self, document_id: UUID) -> None:
        self._tracked_documents.discard(document_id)
        self._pending_discards.discard(document_id)
        self._submit(self._store.discard, document_id)

    def _submit(
        self, function: Callable[..., object], /, *args: object, **kwargs: object
    ) -> Future[object] | None:
        try:
            future = self._executor.submit(function, *args, **kwargs)
        except RuntimeError:
            # close() already shut the worker down (possibly from another
            # thread); dropping the task at shutdown is the best outcome.
            return None
        self._outstanding.add(future)
        future.add_done_callback(self._forget_outstanding)
        return future

    def _forget_outstanding(self, future: Future[object]) -> None:
        self._outstanding.discard(future)


__all__ = ["AutosaveController"]
