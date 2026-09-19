# 14: Publish derived view slices from the projection

**What to build:** `GraphViewModel` is a wide contract (14-field `NodeViewModel` + connections), and the derived facts that are pure functions of the projection are re-derived by four consumers with four different caching strategies: `_transport_target` re-scans `is_source` on every transport press (uncached), `_refresh_action_states` caches source IDs and MIDI eligibility keyed on `session.revision`, `_image_dock_preview_sources` is keyed on the document refresh, and `compute_demand_roots` re-scans all nodes and connections on every debounced activation. `session.revision` exists only to serve the window's private cache, and the session even reads its own published projection back to compare `preview_visible` (`set_connection_preview_visible`) — a consumer desynchronizing from the projection is possible by construction, and the cache-tracking correctness is observable only through full-window tests.

**Solution:** The projection (or a small derived-views struct on the session) publishes the derived slices once per change — source IDs, image-dock source keys, demand roots, eligibility — so consumers read instead of re-scanning and the `revision`-keyed window caches retire. Settle what `session.revision` means afterwards: a deliberately published change token, or deleted.

**Status:** needs-triage

**Blocked by:** 11

**Files:**
- `src/synesthesia_machine/ui/view_models.py`
- `src/synesthesia_machine/ui/session.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/canvas.py`
- `src/synesthesia_machine/ui/demand_roots.py`

**Acceptance:**
- [ ] Consumers read derived slices; no consumer keeps a cache keyed on its own notion of projection freshness
- [ ] `session.revision` is either a deliberately published change token or removed
- [ ] `compute_demand_roots` and `resolve_transport_target` keep their pure-function test surfaces
- [ ] The canvas's per-toggle connection scan is served from the published slice (or its cost is explicitly accepted in the ticket)
- [ ] `uv run check` green; UI test suite green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (UI-lifecycle scout F4). Shape the derived-views contract in design (which slices, who recomputes, how the preview-dock classification's two encodings — `PreviewDock` on the node vs. the `dock` key in `preview_families.FAMILIES` — settle on one source of truth).
