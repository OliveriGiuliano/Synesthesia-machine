# 05: One engine session

**What to build:** EngineSession is a deep, fully tested module (cursors, folded six-state connection machine, known-stopped cache, restart-outcome mapping) with zero production consumers — the UI re-implements its behaviour in three scattered copies (EngineBridge restart mapping, PreviewRouter cursor dicts, MainWindow _engine_known_stopped + error-signature dedup), and profiling/diagnostics round-trips still execute synchronously on the UI thread, so a hung child can freeze the event loop on the 250 ms profiler tick. The 19.6 KB of EngineSession tests protect a code path the app never runs.

**Solution:** Make EngineSession the production session driver: EngineBridge becomes a Qt shell (task runner, debounce clock, state publication) delegating session semantics to it; MainWindow drops the private known-stopped cache and error-dedup policy in favour of a normalized status view published by the bridge; profiling and diagnostic reads move through the task pool.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/runtime/engine_session.py`
- `src/synesthesia_machine/ui/engine_bridge.py`
- `src/synesthesia_machine/ui/preview_router.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/app/bootstrap.py`

**Acceptance:**
- [x] Tested path becomes the production path
- [x] ADR-0013/0020 semantics live in one Qt-free module
- [x] Hung child can no longer block the event loop
- [x] A second bridge consumer (kiosk, tools) stops re-deriving policy
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-17: Implemented. `EngineSession` (runtime, Qt-free) is now the production
  driver: `EngineBridge` is the Qt shell (task pool, 100 ms debounce clock,
  state publication, preview routing) and delegates cursors, the folded
  six-state connection machine, the known-stopped cache, transport, and the
  ADR-0013/0020 restart policy to the session. `MainWindow` dropped its private
  `_engine_known_stopped` cache; `PreviewRouter` is the stateless routing policy
  over one pumped batch. `poll_status` keeps a client-reported crash status so
  the UI renders exit code and crash-log detail. Full suite (1224 passed),
  `uv run check`, and the offscreen smoke test are green.
