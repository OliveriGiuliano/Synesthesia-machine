# 03: Introduce a typed source-configuration seam

**What to build:** A source node's configuration (a video source's file path, process-every-nth-frame, loop flag) is read by the engine and media layer as a typed value derived from the node's ParameterSpec, rather than re-derived from raw parameter-name strings. Adding or renaming a source parameter updates the definition and one typed config, not a dozen consumers. This is the expand step: the old string-based reads still work in parallel, so nothing breaks.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Typed source-configuration value types exist (e.g. a video-source config) derived from the node's ParameterSpec values.
- [x] The engine can build a source from the typed config; the old raw-parameter-name reads remain available in parallel (expand, not cutover).
- [x] The typed config is computed headlessly from the graph snapshot + definition, testable without the engine.
- [x] No behaviour change to source creation; existing source tests pass.
