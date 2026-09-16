# 15: Shared node runtime scaffolding

**What to build:** Eight files each carry a byte-identical 9-line stateless skeleton (node_id plus no-op reset/close), and nine algorithm runtimes (six synesthesia nodes plus MIDI utilities) each re-implement the same wrapper — call the pure function, translate ValueError to ExpectedNodeError with a node-specific code, and run the same source-clock-domain check that exists seven times with seven different error strings. A protocol or lifecycle change must be hand-propagated across eight files.

**Solution:** One package-level StatelessRuntime base (plus a NoDataRuntime for the source placeholders) in nodes, a PureFunctionRuntime(processor, error_code) adapter for algorithm nodes, and a shared validate_clock helper; node files reduce to pure algorithm plus definition.

**Status:** open

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
- [ ] Lifecycle change = one edit
- [ ] Error contract in one place (AGENTS.md rule)
- [ ] Nine runtimes share one adapter
- [ ] Algorithms stay pure for tests
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
