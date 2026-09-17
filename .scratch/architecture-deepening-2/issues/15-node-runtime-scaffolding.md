# 15: Shared node runtime scaffolding

**What to build:** Eight files each carry a byte-identical 9-line stateless skeleton (node_id plus no-op reset/close), and nine algorithm runtimes (six synesthesia nodes plus MIDI utilities) each re-implement the same wrapper — call the pure function, translate ValueError to ExpectedNodeError with a node-specific code, and run the same source-clock-domain check that exists seven times with seven different error strings. A protocol or lifecycle change must be hand-propagated across eight files.

**Solution:** One package-level StatelessRuntime base (plus a NoDataRuntime for the source placeholders) in nodes, a PureFunctionRuntime(processor, error_code) adapter for algorithm nodes, and a shared validate_clock helper; node files reduce to pure algorithm plus definition.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/base.py` (StatelessRuntime, NoDataRuntime, PureFunctionRuntime, require_same_clock)
- `src/synesthesia_machine/nodes/__init__.py` (facade exports)
- `src/synesthesia_machine/nodes/image/runtime_support.py` (deleted)
- `src/synesthesia_machine/nodes/image/{adjustments,channels,dimensions,filters,utilities}.py`
- `src/synesthesia_machine/nodes/input/{video,camera}.py`
- `src/synesthesia_machine/nodes/synesthesia/{channel_to_pitch,edges_to_pitch,fourier,optical_flow,region_grid,scanline}.py`
- `src/synesthesia_machine/nodes/utility/{core,dynamic,midi,scalar_bridges}.py`
- `src/synesthesia_machine/nodes/visualization/core.py`

**Files:**
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/nodes/image/runtime_support.py`
- `src/synesthesia_machine/nodes/utility/core.py`
- `src/synesthesia_machine/nodes/utility/dynamic.py`
- `src/synesthesia_machine/nodes/utility/midi.py`
- `src/synesthesia_machine/nodes/utility/scalar_bridges.py`
- `src/synesthesia_machine/nodes/input/video.py`
- `src/synesthesia_machine/nodes/input/camera.py`
- `src/synesthesia_machine/nodes/visualization/core.py`
- `src/synesthesia_machine/nodes/synesthesia/*.py (6 clock checks)`

**Acceptance:**
- [x] Lifecycle change = one edit
- [x] Error contract in one place (AGENTS.md rule)
- [x] Nine runtimes share one adapter
- [x] Algorithms stay pure for tests
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

## Answer

Done. `nodes/base.py` now owns the shared scaffolding: `StatelessRuntime` (lifecycle skeleton), `NoDataRuntime` (source placeholder publishing `NoData` for declared output ports), `PureFunctionRuntime` (processor + error_code + per-node exception tuple -> ExpectedNodeError) and `require_same_clock(first, second, message)` — all exported from the `synesthesia_machine.nodes` facade.

- `runtime_support.py` deleted; the image family (AdjustmentRuntime, FilterRuntime and 13 other stateless runtimes) rebased onto `StatelessRuntime`.
- Local skeletons (`_RuntimeBase`, `_MidiRuntimeBase`, `_ScalarBridgeRuntime`, `_VisualizerRuntime`) deleted; utility/visualization runtimes rebased.
- `LoadVideoRuntime`/`LoadCameraRuntime` are now `NoDataRuntime` subclasses declaring their output ports; video/camera files keep only source construction + definition.
- The six synesthesia algorithm runtimes and the three MIDI utilities are `PureFunctionRuntime` subclasses wrapping module-level pure functions.
- Deviation from the ticket's count: `ScanlineRuntime` is stateful (row position) so it keeps its own process/reset/close and only rebases on `StatelessRuntime`; `FourierRuntime` subclasses `PureFunctionRuntime` with a `partial`-bound processor and owns the `FourierShapeCache` (cleared on reset/close). The image family's AdjustmentRuntime/FilterRuntime keep their family-specific input dispatch but now sit on the shared lifecycle skeleton.
- The seven inline clock checks (channel_to_pitch x2, edges_to_pitch, fourier, optical_flow x2, region_grid) now call `require_same_clock` with the original per-node messages, so user-facing error text is unchanged.
- Verified: `uv run check` green, `uv run pytest -q` 1262 passed / 3 skipped, `synmachine --smoke-test` offscreen green. Test APIs unchanged (named runtime classes kept as thin subclasses).
