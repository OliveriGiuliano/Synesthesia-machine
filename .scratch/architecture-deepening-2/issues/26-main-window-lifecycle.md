# 26: Split MainWindow's document-lifecycle cluster

**What to build:** Document replacement (with the unsaved-changes decision flow), recovery offers, recent files, preferences application and retranslation, and window-state persistence are all window methods reachable only through private names — a 2228-line, ~70-method compositor that is the package's test bottleneck (62 file touches in 45 commits) and whose README still describes the pre-EngineBridge architecture.

**Solution:** Extract a DocumentLifecycleController (open / save / recover / recent / replacement confirmation, built on the session and the persistence facade) and a preferences-and-retranslation applier; MainWindow keeps composition, signal routing, and rendering of bridge/session state; refresh ui/README.md to the current architecture.

**Blocked by:** 05, 24

**Status:** resolved

**Files:**
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/document_lifecycle.py (new)`
- `src/synesthesia_machine/ui/README.md`

**Acceptance:**
- [x] Each behaviour testable offscreen, without the window
- [x] Lifecycle bugs stop landing in the compositor
- [x] The hot spot gets a shrinkage path
- [x] Docs stop misrouting future agents
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
