# 11: One owner for the preview pump

**What to build:** `EngineBridge.__init__` accepts a `PreviewRouter` and stores it (`engine_bridge.py:165,174`) but never invokes it — the only uses in the file are the constructor parameter and the assignment; `pump_previews_once` hands raw `PumpedPreviews` to its callback and the actual routing is re-done by `MainWindow._on_pumped_previews`, where the window also assembles the router's inputs (`image_visible` from the dock, `image_dock_sources` from its own `_image_visualizer_source_keys` cache re-derived from the projection on every document change). The bridge's interface thus claims a responsibility it does not have (a reader sees the bridge is responsible for routing and is wrong), and "a preview appears in the dock" bounces window timer → bridge pump → window callback → window router call → scene/panels. Test harnesses pass a `PreviewRouter` into the bridge constructor without it ever mattering.

**Solution:** One owner per pipeline step: the pump step routes — the module that pumps calls the router with its visibility inputs and hands the window only routed sink decisions (or, if routing stays window-side, the dead `preview_router` parameter is deleted from the bridge so the interface stops lying). Either way: no stored-but-never-used dependency, one module that routes a pumped batch, and the routing inputs assembled by that same module.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/ui/engine_bridge.py`
- `src/synesthesia_machine/ui/preview_router.py`
- `src/synesthesia_machine/ui/main_window.py`
- `tests/ui/test_engine_bridge.py`
- `tests/ui/test_preview_router.py`

**Acceptance:**
- [x] No constructor parameter anywhere in the pipeline is stored but never used
- [x] Exactly one module routes a pumped batch; the routing inputs are assembled by that module
- [x] The window applies sink decisions (pill/dock/panel) but performs no routing
- [x] Preview tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (UI-lifecycle scout F2 — the dead seam is the only dead code found in the UI cluster).
- 2026-09-19: Resolved — `EngineBridge.pump_previews_once` is the sole routing owner: it assembles the routing inputs (dock visibility flags, the projection, and the projection-derived image-dock visualizer-source set) and routes the polled batch with its own stateless `PreviewRouter`; the window's `_on_pumped_previews` applies the four `RoutingResult` buckets as a pure sink. The dead `preview_router` constructor parameter and the window's `_image_visualizer_source_keys` helper are deleted. Two-axis review passed; its P3 nits (projection-refresh comment accuracy, stale `PumpedPreviews` re-export) were addressed.
