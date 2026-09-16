"""Production adapters that drive the Qt-free EngineBridge from the main window.

The bridge is testable headlessly because everything that relies on the Qt event
loop is injected as a small protocol. This module provides the real implementations
the editor needs:

- :class:`QtEngineTaskRunner` runs one engine operation on a dedicated worker
  thread (so a slow or hung child process can never block the UI event thread)
  and hops the outcome back onto the UI thread through Qt signals. It coalesces
  by task kind: while a kind is in flight, further submissions of the same kind
  are dropped.
- :class:`QtUiClock` is the one-shot ``QTimer`` that powers the bridge's timed
  activation debounce. The window owns the timer object (it is stopped with the
  window); the clock only decides which callback the timer fires.
"""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from functools import partial
from typing import cast

from PySide6.QtCore import QObject, QTimer, Signal


class _EngineTaskSignals(QObject):
    """Cross-thread trampoline: the pool thread emits, the UI thread slots."""

    completed = Signal(str, object, object)
    failed = Signal(str, object, object)


_CallbackPair = tuple[Callable[[str, object], None], Callable[[str, object], None]]


class QtEngineTaskRunner:
    """Production ``EngineTaskRunner``: one-worker pool plus a UI-thread signal hop."""

    def __init__(self, parent: QObject) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="synmachine-ui-engine"
        )
        self._signals = _EngineTaskSignals(parent)
        self._signals.completed.connect(self._on_completed)
        self._signals.failed.connect(self._on_failed)
        self._inflight: set[str] = set()
        self._closed = False

    @property
    def inflight(self) -> set[str]:
        """The live set of task kinds currently running (shared with the window)."""
        return self._inflight

    def submit(
        self,
        kind: str,
        operation: Callable[[], object],
        *,
        on_result: Callable[[str, object], None],
        on_error: Callable[[str, object], None],
    ) -> bool:
        if self._closed or kind in self._inflight:
            return False
        self._inflight.add(kind)
        callbacks = (on_result, on_error)
        future = self._executor.submit(operation)
        # If the operation already completed, the done callback runs on this
        # (UI) thread and the signal hop below is a direct delivery; otherwise
        # it runs on the worker thread and Qt queues the hop to the UI thread.
        future.add_done_callback(partial(self._publish, kind, callbacks))
        return True

    def close(self) -> None:
        """Drain in-flight operations (the engine client is still open) and stop."""
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._inflight.clear()

    def _publish(self, kind: str, callbacks: _CallbackPair, future: Future[object]) -> None:
        try:
            result = future.result()
        except BaseException as error:
            with suppress(RuntimeError):
                self._signals.failed.emit(kind, error, callbacks)
            return
        with suppress(RuntimeError):
            self._signals.completed.emit(kind, result, callbacks)

    def _on_completed(self, kind: str, result: object, callbacks: object) -> None:
        on_result, _ = cast("_CallbackPair", callbacks)
        self._inflight.discard(kind)
        on_result(kind, result)

    def _on_failed(self, kind: str, error: object, callbacks: object) -> None:
        _, on_error = cast("_CallbackPair", callbacks)
        self._inflight.discard(kind)
        on_error(kind, error)


class QtUiClock:
    """Production ``UiClock``: one-shot ``QTimer`` around a window-owned timer."""

    def __init__(self, timer: QTimer) -> None:
        self._timer = timer
        self._callback: Callable[[], None] | None = None
        timer.setSingleShot(True)
        timer.timeout.connect(self._fire)

    def schedule_once(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self._timer.setInterval(delay_ms)
        self._callback = callback
        self._timer.start(delay_ms)

    def cancel(self, callback: Callable[[], None]) -> None:
        if self._callback is callback:
            self._callback = None
            self._timer.stop()

    def _fire(self) -> None:
        callback = self._callback
        self._callback = None
        if callback is not None:
            callback()


__all__ = ["QtEngineTaskRunner", "QtUiClock"]
