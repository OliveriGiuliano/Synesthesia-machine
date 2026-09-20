# 14: Publish derived view slices from the projection

**What to build:** `GraphViewModel` is a wide contract (14-field `NodeViewModel` + connections), and the derived facts that are pure functions of the projection are re-derived by four consumers with four different caching strategies: `_transport_target` re-scans `is_source` on every transport press (uncached), `_refresh_action_states` caches source IDs and MIDI eligibility keyed on `session.revision`, `_image_dock_preview_sources` is keyed on the document refresh, and `compute_demand_roots` re-scans all nodes and connections on every debounced activation. `session.revision` exists only to serve the window's private cache, and the session even reads its own published projection back to compare `preview_visible` (`set_connection_preview_visible`) — a consumer desynchronizing from the projection is possible by construction, and the cache-tracking correctness is observable only through full-window tests.

**Solution:** The projection (or a small derived-views struct on the session) publishes the derived slices once per change — source IDs, image-dock source keys, demand roots, eligibility — so consumers read instead of re-scanning and the `revision`-keyed window caches retire. Settle what `session.revision` means afterwards: a deliberately published change token, or deleted.

**Status:** completed

**Blocked by:** 11

**Files:**
- `src/synesthesia_machine/ui/view_models.py`
- `src/synesthesia_machine/ui/session.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/canvas.py`
- `src/synesthesia_machine/ui/demand_roots.py`

**Acceptance:**
- [x] Consumers read derived slices; no consumer keeps a cache keyed on its own notion of projection freshness
- [x] `session.revision` removed: nothing consumed it once the window caches moved onto projection object identity
- [x] `compute_demand_roots` and `resolve_transport_target` keep their pure-function test surfaces (both now read published slices)
- [x] The canvas's per-toggle connection scan is served from the published visibilities mapping
- [x] `uv run check` green; UI test suite green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (UI-lifecycle scout F4). Shape the derived-views contract in design (which slices, who recomputes, how the preview-dock classification's two encodings — `PreviewDock` on the node vs. the `dock` key in `preview_families.FAMILIES` — settle on one source of truth).
- 2026-09-20: Implemented. `project_graph` now builds a `DerivedViews` slice (source/sink ids,
  image/note visualizer ids, image-dock source keys, pill producers, preview visibilities, source
  facts) attached to `GraphViewModel.derived`, computed once per projection. `compute_demand_roots`
  reads the slices; the engine bridge's identity-keyed image-dock cache is gone (the pump reads the
  published slice); the canvas serves both lookups from the published mapping; `session._revision`
  and `session.revision` are deleted; `PreviewFamilySpec.dock` is unified on the headless
  `PreviewDock` enum (FAMILIES and the node declarations now share one source of truth;
  `display_visualizer` still returns the string key for the window's dock lookup).
  Review-driven corrections (two-axis review, both reviewers): (1) the window's MIDI-eligibility
  cache is keyed on the projection object's *identity*, not on `(source_facts, graph_valid)` —
  `_scan_export_nodes` reads the full node set (MIDI-output membership, source parameters, file
  stats), so a subset key goes stale on sink add/remove; every session refresh publishes a new
  projection object and the window keeps the cached one alive, so identity is a sound token that
  reproduces the old revision-key semantics. (2) `set_connection_preview_visible` keeps the
  explicit absent-connection guard (stale ids are a deliberate no-op, not a `KeyError` from the
  command). (3) `DerivedViews.preview_visibilities` is a `Mapping` frozen with `MappingProxyType`
  in `__post_init__` (precedent: `GraphSnapshot.document_settings`).
  Verification: UI suite 304 passed; `uv run check` green; offscreen smoke test clean; full suite
  1441 passed (one pre-existing flake in `tests/runtime/test_process_previews.py`, passes in
  isolation, unrelated to the UI-only diff).
