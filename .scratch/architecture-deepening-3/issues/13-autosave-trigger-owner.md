# 13: Autosave trigger policy with the autosave controller

**What to build:** "A document edit leads to an autosave" takes four hops: `session.changed` → `MainWindow._schedule_autosave` → the window-owned `_autosave_timer` (interval from user preference; also started/stopped from `_on_dirty_changed`) → `document_lifecycle.autosave_now` → `AutosaveController.request`. The controller is a deep, headlessly tested writer — it receives the session and already handles discards on replacement — but the *when*-to-save policy (debounce delay, dirty gating) lives in the window's timer pair, so the trigger is effectively untestable without a `QMainWindow` and a real `QTimer`, and any change to autosave cadence lands in the repository's most-touched file.

**Solution:** The controller owns the trigger: dirty + delay → `request`, with the debounce timer (and the preference interval) living inside the controller; the window starts/stops the controller's cadence (or delegates it entirely) and keeps no autosave timer of its own.

**Status:** done

**Files:**
- `src/synesthesia_machine/ui/autosave_controller.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/document_lifecycle.py`
- `src/synesthesia_machine/ui/README.md`
- `tests/ui/test_autosave.py`
- `tests/ui/test_settings_and_recent_files.py`
- `tests/ui/test_process_supervision.py`

**Acceptance:**
- [x] The trigger policy (dirty gating, debounce delay) lives in the controller and is testable offscreen without a `QTimer`
- [x] The window owns no autosave timer
- [x] Existing autosave behaviour (debounced write on edit, discard handling on replacement) is unchanged and covered by headless tests
- [x] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (UI-lifecycle scout F5, first half). The `closeEvent` shutdown orchestration is deliberately out of scope: tearing down the Qt shell is the window's job as composition root.
- 2026-09-20: Done. The controller now owns the whole autosave behaviour of the watched session: `schedule()` re-arms a one-shot debounce on every `session.changed` emission (dirty gate inside), `_on_document_dirty_changed` re-arms on the dirty transition and cancels the pending debounce on the clean transition (still flushing settled discards), and `close()` cancels the debounce before the bounded drain. The debounce fire callback is `autosave_now()`, which also serves the synchronous pre-replacement save; the lifecycle's `autosave_now` is now a delegate to it, so the host protocol is unchanged. Seam: the controller takes an optional `UiClock` (the engine-bridge protocol; production default is the `QtUiClock` wrapper around a controller-owned `QTimer` that `close()` stops) and an optional `delay_provider` callable — the window passes `lambda: self.preferences.autosave_delay_seconds`, so a preference change takes effect from the next re-arm and an armed debounce keeps its delay. `close()`/cancel rely on a stable callback reference (`self._due = self.autosave_now`), because the clocks compare callbacks by identity. The window keeps no autosave timer: the `changed`/`dirtyChanged` trigger connections and the timer lifecycle moved to the controller; `_on_dirty_changed` only mirrors the dirty flag onto the title bar. Tests drive the policy through a recording `UiClock` double (arm-on-edit, clean-noop, cancel-on-clean, provider read at schedule time) without any `QTimer`; the window-level settings test now verifies the applied delay through the controller's clock, and the process-supervision frozen-timer list dropped `_autosave_timer`.
