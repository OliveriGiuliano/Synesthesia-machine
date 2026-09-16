# 09: Extract the UI PreviewRouter

**What to build:** Live previews reaching the canvas connection pills and the preview docks flow through one UI router that owns the per-port sequence cursors and routes each preview to the right pill/dock, instead of a four-hop chain through the window, a converter, the scene, and the graphics items. A maintainer changes pill/dock routing in one place, and preview routing is testable against a fake channel.

**Blocked by:** 02 (Consolidate the engine-side preview transport), 07 (Extract the EngineBridge), 08 (Add an EngineSession facade)

**Status:** done — `ui/preview_router.py` `PreviewRouter` owns the three per-port cursors + a pure Qt-free `route()` decision (pill / image-dock / note-dock / value-pill); `EngineBridge` takes the router, `MainWindow` applies the `RoutingResult` to scene/panels; 7 headless router tests + 197 UI tests + ruff/Pyright clean

- [x] A PreviewRouter owns per-port cursors and routes image/channel/scalar/note previews to the canvas pills and the preview docks.
- [x] The prior poll → convert → scene → graphics chain is replaced by the router; the window no longer holds the sequence-cursor dicts or the 4-file routing.
- [x] Preview routing is testable against the PreviewChannel with a fake transport (no child spawn).
- [x] Live preview behaviour (producer-port anchoring, cadence, dock-vs-pill routing) is unchanged.
