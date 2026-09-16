# 05: One engine session

**What to build:** EngineSession is a deep, fully tested module (cursors, folded six-state connection machine, known-stopped cache, restart-outcome mapping) with zero production consumers — the UI re-implements its behaviour in three scattered copies (EngineBridge restart mapping, PreviewRouter cursor dicts, MainWindow _engine_known_stopped + error-signature dedup), and profiling/diagnostics round-trips still execute synchronously on the UI thread, so a hung child can freeze the event loop on the 250 ms profiler tick. The 19.6 KB of EngineSession tests protect a code path the app never runs.

**Solution:** Make EngineSession the production session driver: EngineBridge becomes a Qt shell (task runner, debounce clock, state publication) delegating session semantics to it; MainWindow drops the private known-stopped cache and error-dedup policy in favour of a normalized status view published by the bridge; profiling and diagnostic reads move through the task pool.

**Status:** open

**Files:**
- `src/synesthesia_machine/runtime/engine_session.py`
- `src/synesthesia_machine/ui/engine_bridge.py`
- `src/synesthesia_machine/ui/preview_router.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/app/bootstrap.py`

**Acceptance:**
- [ ] Tested path becomes the production path
- [ ] ADR-0013/0020 semantics live in one Qt-free module
- [ ] Hung child can no longer block the event loop
- [ ] A second bridge consumer (kiosk, tools) stops re-deriving policy
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
