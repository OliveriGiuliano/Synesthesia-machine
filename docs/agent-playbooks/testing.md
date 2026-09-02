# Testing playbook

Use the narrowest relevant domain suite while iterating, then widen coverage according to risk.

| Area changed | Start with |
| --- | --- |
| Dependency direction and registry composition | `tests/architecture/` |
| Immutable values and client/process contracts | `tests/contracts/` |
| Authoring model, validation, layout, and compiler | `tests/graph/` |
| Graph JSON, clipboard, backups, recovery, relinking, and migrations | `tests/persistence/` |
| Video and camera behavior | `tests/media/` |
| MIDI output and debug synthesis | `tests/midi/` |
| Image, synesthesia, and utility definitions/runtimes | `tests/nodes/` |
| Scheduler, engine clients, IPC, previews, profiling, and native threads | `tests/runtime/` |
| Editor shell, commands, transport, settings, previews, and profiler UI | `tests/ui/` |
| Diagnostic bundles and privacy rules | `tests/diagnostics/` |
| Cross-domain saved-graph and lifecycle behavior | `tests/integration/` |
| Benchmark and soak harnesses | `tests/benchmarks/` |
| Release configuration, packaged smoke, and archive reproducibility | `tests/packaging/` |
| Dependency/environment adapters and bootstrap smoke | `tests/smoke/` |

## Testing rules

- Use exact equality for discrete graph and MIDI results where possible. Use
  `numpy.testing.assert_allclose` with a deliberate node-specific tolerance for numeric image work.
- Assert semantic outcomes in addition to shapes or opaque golden data: metadata, ranges, edge
  positions, frequency peaks, note state, lifecycle calls, and source clocks.
- Assert negative behavior as well as the happy path: unrelated consumers remain unchanged, stale
  data is absent, invalid values are rejected, and a failed replacement preserves working state.
- Use deterministic UUIDs, seeds, arrays, generated videos, clocks, and timestamps. Do not depend on
  ordering accidents, wall-clock sleeps, the network, or local hardware.
- Put temporary files under pytest's `tmp_path`. Process tests must clean up clients and shared memory
  in `finally` blocks or fixtures.
- A behavior fix needs a regression test that fails for the original defect. Do not weaken an existing
  assertion merely to make a new implementation pass.
- Shared factories and conformance assertions belong in `tests/support`; compatibility inputs belong
  in `tests/fixtures/compatibility`. Neither is a user-facing example.
- `uv run check` applies strict Pyright to `src` and `tools`. CI additionally runs
  `uv run pyright --project pyright-tests.json` over tests. Run that call-site gate after changing a
  public protocol.
- Run `uv run check` after code changes. Before final handoff, run `uv run pytest -q` unless the task
  is documentation-only or the suite cannot reasonably run; report any unrun gate and why.

CI runs the quality job on `windows-latest` and `ubuntu-latest` with `QT_QPA_PLATFORM=offscreen`,
installs the locked development group, runs both type gates, executes the full suite, and launches
the real application-shell smoke test. Local success on one platform is not evidence for the other
platform's spawn, devices, Qt deployment, or filesystem behaviour.
