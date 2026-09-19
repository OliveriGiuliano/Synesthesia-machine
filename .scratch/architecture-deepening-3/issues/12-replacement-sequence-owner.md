# 12: Replacement sequence through the controller on every path

**What to build:** `DocumentLifecycleController`'s docstring claims it is "the single owner of the recent-file list and of the replacement decision flow" — `new_document` and `open_path` implement the sequence (capture `previous_id` → `confirm_replacement()` → replace → `if DISCARD: discard_recovery(previous_id)`). But `MainWindow.randomize_nodes` with no selection re-implements the identical sequence inline: `previous_id = …` → `self.document_lifecycle.confirm_replacement()` → `self.session.replace_with_snapshot(snapshot)` → `if DISCARD: self.document_lifecycle.discard_recovery(previous_id)` — calling the session's replace directly and bypassing the controller. The "discard recovery after a DISCARD replacement" cleanup rule now has two sites with no shared seam: a change to the decision flow (a new outcome, different recovery cleanup) must be made in two modules, and the two copies are verified by different test regimes (the controller's scripted-host tests vs. full-window editor tests), so they can silently drift.

**Solution:** Give the controller a replacement entry point that takes an already-built snapshot (or routes the window's generate path through the controller's replacement flow), so every document-replacement path — open, new, generate, and any future path — calls the controller; the window's inline sequence is deleted.

**Status:** ready-for-agent

**Files:**
- `src/synesthesia_machine/ui/document_lifecycle.py`
- `src/synesthesia_machine/ui/main_window.py`
- `tests/ui/test_document_lifecycle.py`

**Acceptance:**
- [ ] No replacement decision sequence is written inline outside the controller
- [ ] The scripted-host tests cover the replace-with-existing-snapshot path (the randomize flow)
- [ ] Full-window tests pass with the window delegating
- [ ] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (UI-lifecycle scout F3).
