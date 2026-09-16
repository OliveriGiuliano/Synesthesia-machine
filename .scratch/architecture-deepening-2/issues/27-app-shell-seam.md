# 27: One app-shell seam for harnesses and tools

**What to build:** Assembly knowledge exists in five-plus places: bootstrap.main is canonical, but reference_benchmark, process_ui_diagnostic, and ui_diagnostic each re-assemble QApplication + registry + engine client + MainWindow with their own timeout and QSettings wiring, and the MainWindow constructor signature is known to the app and every tool. The tools then poke window internals — one reads session.view_model.nodes, one drives image_preview_dock, the other image_preview_panel (the attribute families already drifted) — and ui_diagnostic / process_ui_diagnostic are ~40 KB of near-duplicate harnesses.

**Solution:** One app-side seam (e.g. create_app_shell(paths, engine_client_factory, ...) -> (application, window)) reused by bootstrap.main and the tools, plus a small driver/observation API on the window (open graph, play, query node titles, preview visibility); merge the two UI diagnostic tools behind a --engine in-process|spawned flag, keeping the in-process mode explicitly diagnostic-only.

**ADR:** ADR-0005 is untouched for the app; the in-process-engine mode of the merged diagnostic tool stays diagnostic-only (that tension is noted in the tool, not the app).

**Blocked by:** 26

**Status:** open

**Files:**
- `src/synesthesia_machine/app/bootstrap.py`
- `src/synesthesia_machine/app/application.py`
- `src/synesthesia_machine/app/settings.py`
- `tools/reference_benchmark.py`
- `tools/process_ui_diagnostic.py`
- `tools/ui_diagnostic.py`

**Acceptance:**
- [ ] MainWindow constructor known in one place
- [ ] One fix repairs all four harnesses
- [ ] Drift channel between the twins closed
- [ ] Tools become consumers of a stable seam
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
