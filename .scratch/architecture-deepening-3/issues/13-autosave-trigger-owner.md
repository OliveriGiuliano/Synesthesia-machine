# 13: Autosave trigger policy with the autosave controller

**What to build:** "A document edit leads to an autosave" takes four hops: `session.changed` → `MainWindow._schedule_autosave` → the window-owned `_autosave_timer` (interval from user preference; also started/stopped from `_on_dirty_changed`) → `document_lifecycle.autosave_now` → `AutosaveController.request`. The controller is a deep, headlessly tested writer — it receives the session and already handles discards on replacement — but the *when*-to-save policy (debounce delay, dirty gating) lives in the window's timer pair, so the trigger is effectively untestable without a `QMainWindow` and a real `QTimer`, and any change to autosave cadence lands in the repository's most-touched file.

**Solution:** The controller owns the trigger: dirty + delay → `request`, with the debounce timer (and the preference interval) living inside the controller; the window starts/stops the controller's cadence (or delegates it entirely) and keeps no autosave timer of its own.

**Status:** ready-for-agent

**Files:**
- `src/synesthesia_machine/ui/autosave_controller.py`
- `src/synesthesia_machine/ui/main_window.py`
- `tests/ui/test_autosave.py`

**Acceptance:**
- [ ] The trigger policy (dirty gating, debounce delay) lives in the controller and is testable offscreen without a `QTimer`
- [ ] The window owns no autosave timer
- [ ] Existing autosave behaviour (debounced write on edit, discard handling on replacement) is unchanged and covered by headless tests
- [ ] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (UI-lifecycle scout F5, first half). The `closeEvent` shutdown orchestration is deliberately out of scope: tearing down the Qt shell is the window's job as composition root.
