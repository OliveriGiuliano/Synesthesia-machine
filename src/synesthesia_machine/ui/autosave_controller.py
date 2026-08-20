"""Coalesced background autosave orchestration for the Qt document session."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from synesthesia_machine.graph import GraphSnapshot
from synesthesia_machine.persistence.autosave import AutosaveStore


@dataclass(frozen=True, slots=True)
class _AutosaveRequest:
    snapshot: GraphSnapshot
    explicit_path: Path | None


class AutosaveController(QObject):
    """Write immutable snapshots off the UI thread and retain only the newest pending one."""

    saved = Signal(object)
    failed = Signal(object)
    _finished = Signal(object, object)

    def __init__(self, store: AutosaveStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="synmachine-ui-autosave",
        )
        self._future: Future[Path] | None = None
        self._pending: _AutosaveRequest | None = None
        self._closed = False
        self._finished.connect(self._on_finished)

    def request(self, snapshot: GraphSnapshot, *, explicit_path: Path | None) -> None:
        if self._closed:
            return
        request = _AutosaveRequest(snapshot, explicit_path)
        if self._future is not None:
            self._pending = request
            return
        self._start(request)

    def discard(self, document_id: UUID) -> None:
        if self._closed:
            return
        if self._pending is not None and self._pending.snapshot.document_id == document_id:
            self._pending = None
        self._executor.submit(self._store.discard, document_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _start(self, request: _AutosaveRequest) -> None:
        future = self._executor.submit(
            self._store.save,
            request.snapshot,
            explicit_path=request.explicit_path,
        )
        self._future = future
        future.add_done_callback(self._publish_result)

    def _publish_result(self, future: Future[Path]) -> None:
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
        if isinstance(result, Path):
            self.saved.emit(result)
        elif isinstance(result, BaseException):
            self.failed.emit(result)
        request = self._pending
        self._pending = None
        if request is not None and not self._closed:
            self._start(request)


__all__ = ["AutosaveController"]
