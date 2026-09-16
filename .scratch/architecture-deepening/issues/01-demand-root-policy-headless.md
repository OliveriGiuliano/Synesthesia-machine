# 01: Extract the demand-root policy headless

**What to build:** The graph's demand-root selection — which nodes the engine must execute given the current graph and which preview surfaces are visible — is computed and verified without a running editor window. A maintainer changes the ADR-0008/0011 demand policy (a new visualizer kind, a new pill type) and sees it covered by headless tests that assert the exact demand-root set for a given graph + dock-visibility state.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Demand roots are computed by a Qt-free function of (graph snapshot, node registry, image-dock visibility, note-dock visibility) returning the same set the editor currently derives: SINK anchors, note-visualizer anchors, image/channel display anchors, and visible connection-pill producers.
- [x] Headless tests assert the demand-root set for representative graphs (sinks, note visualizers, display nodes, visible vs hidden pills) without instantiating any Qt window or the engine.
- [x] The editor's activation path calls this function, so engine execution is unchanged.
- [x] The demand-root module imports no Qt.
