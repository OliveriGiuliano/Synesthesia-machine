# Testing playbook

Use the narrowest relevant tests during development, then widen coverage according to risk.

| Area changed | Start with |
| --- | --- |
| Runtime values, definitions, graph, compiler, scheduler, persistence boundaries | `tests/phase1/` |
| Editor shell, commands, clipboard, autosave, session | `tests/phase2/` |
| Video vertical slice, previews, debug synth, in-process client | `tests/phase3/` |
| Spawned engine, plan swap, camera, MIDI, shared memory, supervision | `tests/phase4/` |
| Image/channel nodes, immutability, catalogue, soak behavior | `tests/phase5/` |
| Synesthesia algorithms, MIDI utilities, examples, benchmarks | `tests/phase6/` |
| Groups/layout, settings, recovery, relinking, migrations, large graph | `tests/phase7/` |
| Profiling, diagnostics, benchmark/soak harnesses, native-thread behavior | `tests/phase8/` |
| Release configuration, packaged smoke, archive reproducibility | `tests/phase9/` |
| Dependency/environment adapters and bootstrap smoke | `tests/smoke/` |
| Cross-phase requested behavior | Root-level `tests/test_*.py` files |

## Testing rules

- Use exact equality for discrete graph and MIDI results where possible. Use
  `numpy.testing.assert_allclose` with a deliberate node-specific tolerance for numeric image work.
- Assert semantic outcomes in addition to shapes or opaque golden data: metadata, ranges, edge
  positions, frequency peaks, note state, lifecycle calls, and source clocks.
- Assert negative behavior as well as the happy path: an unrelated consumer remains unchanged, stale
  data is absent, a threshold suppresses the current value, and invalid replacement preserves the
  working state where applicable.
- Use deterministic UUIDs, seeds, arrays, generated videos, clocks, and timestamps. Do not make tests
  depend on ordering accidents, wall-clock sleeps, the network, or local hardware.
- Put temporary files under pytest's `tmp_path`. Process tests must clean up clients and shared memory
  in `finally` blocks or fixtures.
- A behavior fix needs a regression test that fails for the original defect. Do not weaken an existing
  assertion merely to make a new implementation pass.
- `pyproject.toml` currently includes `src` and `tools`, but not `tests`, in strict Pyright. Treat test
  call-site types as manually unverified: audit changed keys, mappings, IDs, and protocol signatures,
  and do not assume `uv run check` would catch an incompatible test argument.
- Run `uv run check` after code changes. Before final handoff, run `uv run pytest -q` unless the task is
  documentation-only or the suite cannot reasonably run; report any unrun gate and why.

CI on `windows-latest` sets `QT_QPA_PLATFORM=offscreen`, installs the locked development group, then
runs `uv run check` and `uv run pytest -q`. Local success on another platform is not evidence that
Windows spawn, devices, Qt deployment, or case-insensitive filesystem behavior is correct.
