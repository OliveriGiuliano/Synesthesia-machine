# Synesthesia Machine

Synesthesia Machine is a Windows desktop instrument that transforms video and camera data into
live MIDI note states through a typed visual node graph.

## Requirements

- Windows 11 x64
- [`uv`](https://docs.astral.sh/uv/)
- No system Python is required; `uv` manages CPython 3.12 for this project.

## Set up

```powershell
uv python install 3.12
uv sync --locked
```

The committed `.python-version` constrains commands to Python 3.12. The synchronized environment
is stored in the ignored project-local `.venv` directory.

## Development commands

```powershell
uv run synmachine                    # Open the node editor
uv run synmachine --smoke-test       # Start and stop the real application shell
uv run check                         # Ruff format/lint and strict Pyright
uv run test                          # Complete automated test suite
uv run python -m tools.phase3_performance  # 60-second Phase 3 diagnostic
uv run python -m tools.phase4_performance  # Process-backed Phase 4 diagnostic
uv run python -m tools.phase5_soak         # 30-minute-equivalent Phase 5 memory soak
uv run python -m tools.phase6_benchmarks   # Deterministic 500x500 algorithm diagnostics
uv run python -m tools.phase7_recovery     # Forced-exit autosave/recovery proof
uv run python -m tools.phase7_validate_graphs examples  # Migration-aware graph validation
uv run python -m tools.phase7_large_graph  # 500-node editor responsiveness evidence
```

Hardware spikes are documented in [`tools/README.md`](tools/README.md). Automated tests use
generated media and mocks; they never require a camera, MIDI port, or audio output device.

## Examples

### Phase 3 Hue Chord

Open [`examples/phase3/hue_chord.synmachine.json`](examples/phase3/hue_chord.synmachine.json) and
press **Play** to run the saved video → image/channel processing → desired MIDI state → image/note
visualization graph. Debug audio is disabled in the saved file and can be enabled explicitly on its
Generate Audio node. See the [example instructions](examples/phase3/README.md) and the
[Phase 3 completion report](docs/phase-3-completion-report.md) for behavior, evidence, and measured
performance.

Phase 4 preserves the same portable graph while running it behind `ProcessEngineClient` and
`EngineServer`. See the [Phase 4 completion report](docs/phase-4-completion-report.md) for process,
camera, MIDI, lifecycle, and performance evidence.

### Phase 5 catalogue and reference graph

[`examples/phase5`](examples/phase5) contains two persisted acceptance artifacts:

- [`reference_graph.synmachine.json`](examples/phase5/reference_graph.synmachine.json) is an
  executable deterministic Load Video → Resize → Gaussian Blur → HSV graph. It branches through
  Separate Channels → Channel to Pitch → Note Visualizer and through Canny → Channel Display.
- [`catalogue.synmachine.json`](examples/phase5/catalogue.synmachine.json) instantiates every one of
  the 51 built-in registry definitions exactly once. It is intentionally disconnected and is a
  persistence/instantiation smoke artifact rather than an executable graph.

The Phase 5 acceptance tool drives a production-compiled Load Video → Hold Image → Display Image
Data path for 108,000 accelerated ticks, equivalent to 30 minutes at 60 FPS. Its sampled process-RSS
and exact retained-capacity evidence is committed in
[`docs/phase-5-soak.json`](docs/phase-5-soak.json). See the
[Phase 5 completion report](docs/phase-5-completion-report.md) for the node batches, shared policies,
tests, limitations, and measurements.

### Phase 6 synesthesia examples and catalogue

[`examples/phase6`](examples/phase6) contains hardware-safe Motion Grid, Edge Ensemble, Scanning Score,
and Spatial Spectrum graphs plus the current exact 65-node catalogue. The examples use the included
deterministic video and Note Visualizer or disabled Generate Audio sinks; none opens a MIDI port.

The repeatable Phase 6 diagnostic times all four synthesis algorithms on deterministic 500×500
fixtures and commits stable output-note states plus median/p95 samples in
[`docs/phase-6-benchmarks.json`](docs/phase-6-benchmarks.json). It is algorithm-level evidence, not a
full graph-throughput claim. See the [node reference](docs/phase-6-node-reference.md),
[example instructions](examples/phase6/README.md), and
[Phase 6 completion report](docs/phase-6-completion-report.md).

### Phase 7 daily-use editor robustness

Phase 7 keeps all existing graphs and runtime behavior while making the editor safer for regular
technical work. Canvas groups/comments and layout changes share exact undo/redo; saves use atomic
replacement with one previous-generation backup; dirty work has a 60-second default recovery path;
and Load Video media can be explicitly relinked with stored size/fingerprint checks. Validation issues
are continuously visible and navigable, and large graphs use incremental scene synchronization, lazy
parameter editors, and low-zoom detail suppression.

Canonical evidence is committed in
[`docs/phase-7-recovery.json`](docs/phase-7-recovery.json) and
[`docs/phase-7-large-graph.json`](docs/phase-7-large-graph.json). See the
[Phase 7 completion report](docs/phase-7-completion-report.md) for persistence behavior, migration
fixtures, test results, measurements, and limitations.

## Architecture

The implementation contract is
[`synesthesia_machine_design/00_master_architecture.md`](synesthesia_machine_design/00_master_architecture.md).
Any deviation from that baseline requires an Architecture Decision Record under [`docs/adr`](docs/adr).

Current module boundaries include:

- `src/synesthesia_machine/contracts`: immutable runtime values, protocol-v14 process messages,
  compact node-memory diagnostics, and the final-shaped `EngineClient` API;
- `src/synesthesia_machine/graph`, `nodes`, and `runtime`: Qt-free graph compilation, atomic plan
  replacement, process supervision, bounded source mailboxes, and shared-preview transport;
- `src/synesthesia_machine/media` and `midi`: deterministic vectorized/OpenCV image processing,
  musical mapping, reconnecting camera capture, persistent exact-port MIDI output, and debug
  synthesis;
- `src/synesthesia_machine/ui`: the PySide6 editor, productivity commands, validation navigation,
  source-scoped transport, status, and preview adapters;
- `examples/phase3`, `examples/phase5`, and `examples/phase6`: portable executable graphs,
  deterministic media, historical/current all-definition catalogue smoke artifacts, and safe Phase 6
  algorithm examples;
- `tests`: hardware-independent Phase 1–9 and smoke acceptance coverage.
