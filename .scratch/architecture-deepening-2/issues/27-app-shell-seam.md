# 27: One app-shell seam for harnesses and tools

**What to build:** Assembly knowledge exists in five-plus places: bootstrap.main is canonical, but reference_benchmark, process_ui_diagnostic, and ui_diagnostic each re-assemble QApplication + registry + engine client + MainWindow with their own timeout and QSettings wiring, and the MainWindow constructor signature is known to the app and every tool. The tools then poke window internals — one reads session.view_model.nodes, one drives image_preview_dock, the other image_preview_panel (the attribute families already drifted) — and ui_diagnostic / process_ui_diagnostic are ~40 KB of near-duplicate harnesses.

**Solution:** One app-side seam (e.g. create_app_shell(paths, engine_client_factory, ...) -> (application, window)) reused by bootstrap.main and the tools, plus a small driver/observation API on the window (open graph, play, query node titles, preview visibility); merge the two UI diagnostic tools behind a --engine in-process|spawned flag, keeping the in-process mode explicitly diagnostic-only.

**ADR:** ADR-0005 is untouched for the app; the in-process-engine mode of the merged diagnostic tool stays diagnostic-only (that tension is noted in the tool, not the app).

**Blocked by:** 26

**Status:** resolved

**Files:**
- `src/synesthesia_machine/app/shell.py`
- `src/synesthesia_machine/app/bootstrap.py`
- `src/synesthesia_machine/app/application.py`
- `src/synesthesia_machine/app/settings.py`
- `src/synesthesia_machine/ui/main_window.py`
- `tests/ui/test_process_supervision.py`
- `tools/reference_benchmark.py`
- `tools/ui_diagnostic.py`
- `tools/README.md`

**Acceptance:**
- [x] MainWindow constructor known in one place
- [x] One fix repairs all four harnesses
- [x] Drift channel between the twins closed
- [x] Tools become consumers of a stable seam
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

## Resolution

New `src/synesthesia_machine/app/shell.py` owns the app-shell assembly: `create_app_shell`
builds the shell from an `EngineClientFactory` (the single variable), and
`process_client_factory` / `in_process_client_factory` build the process and in-process
clients with crash-log wiring. `bootstrap.main` now delegates to the seam, and
`MainWindow` grows a small public driver API (`reveal_preview_docks`, `node_titles`) so
tools stop reaching into `image_preview_dock` / `session.view_model.nodes`. `reference_benchmark`
and the merged `ui_diagnostic` consume the seam; the `ui_diagnostic` / `process_ui_diagnostic`
twins are collapsed into one tool with `--engine spawned|in-process` (in-process is
diagnostic-only). The production bootstrap contract test now patches the seam's dependencies in
the `shell` namespace and still asserts the freeze → client → window → show → exec → close order.
