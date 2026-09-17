# 22: Widen the view-model contract and publish the ui facade

**What to build:** The published view model is thinner than the renderers need, so they reach past it: ConnectionViewModel hides preview_visible (the scene re-reads raw connection ui_state with the absent-means-True convention that four modules interpret independently), NodeViewModel hides source-ness (the window scans document plus registry in two places to find sources), groups exist only on the mutable document, and the package itself has no facade — ui/__init__.py is a one-line docstring, so every consumer imports by full module path.

**Solution:** project_graph interprets the ui_state convention once and publishes preview_visible on the connection view, execution kind / is_source on the node view, and a group list on the graph view; scene, window, and demand-roots read only the projection; ui/__init__.py exports the true public surface (session, bridge, router, autosave, actions, view models, translations).

**Blocked by:** 21

**Status:** resolved

**Files:**
- `src/synesthesia_machine/ui/view_models.py`
- `src/synesthesia_machine/ui/canvas.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/demand_roots.py`
- `src/synesthesia_machine/ui/connection_state.py`
- `src/synesthesia_machine/ui/__init__.py`

**Acceptance:**
- [x] What a renderer sees = one projection
- [x] Absent-means-True interpreted in exactly one place
- [x] Window drops its document scans
- [x] Package interface reviewable in one file
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
