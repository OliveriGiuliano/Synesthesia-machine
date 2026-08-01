# Synesthesia Machine

Synesthesia Machine is a Windows desktop instrument that transforms video and camera data into
live MIDI note states through a typed visual node graph.

**Phases 0–4 are complete.** Production UI composition now supervises a spawned engine process for
runtime execution, bounded shared-memory previews, camera capture, MIDI output, debug audio, and
profiling. Live camera behavior is covered with deterministic simulation because no physical camera
was available; an explicitly selected loopMIDI port was exercised separately from automated tests.

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
```

Hardware spikes are documented in [`tools/README.md`](tools/README.md). Automated tests use
generated media and mocks; they never require a camera, MIDI port, or audio output device.

## Phase 3 example

Open [`examples/phase3/hue_chord.synmachine.json`](examples/phase3/hue_chord.synmachine.json) and
press **Play** to run the saved video → image/channel processing → desired MIDI state → image/note
visualization graph. Debug audio is disabled in the saved file and can be enabled explicitly on its
Generate Audio node. See the [example instructions](examples/phase3/README.md) and the
[Phase 3 completion report](docs/phase-3-completion-report.md) for behavior, evidence, and measured
performance.

Phase 4 preserves the same portable graph while running it behind `ProcessEngineClient` and
`EngineServer`. See the [Phase 4 completion report](docs/phase-4-completion-report.md) for process,
camera, MIDI, lifecycle, and performance evidence.

## Architecture

The implementation contract is
[`synesthesia_machine_design/00_master_architecture.md`](synesthesia_machine_design/00_master_architecture.md).
Any deviation from that baseline requires an Architecture Decision Record under [`docs/adr`](docs/adr).

Current module boundaries include:

- `src/synesthesia_machine/contracts`: immutable runtime values, versioned process messages, and the
  final-shaped `EngineClient` API;
- `src/synesthesia_machine/graph`, `nodes`, and `runtime`: Qt-free graph compilation, atomic plan
  replacement, process supervision, bounded source mailboxes, and shared-preview transport;
- `src/synesthesia_machine/media` and `midi`: deterministic video/image processing, musical
  mapping, reconnecting camera capture, persistent exact-port MIDI output, and debug synthesis;
- `src/synesthesia_machine/ui`: the PySide6 editor, transport, status, and preview adapters;
- `examples/phase3`: the portable Hue Chord graph and generated media fixture;
- `tests`: hardware-independent Phase 1–4 and smoke acceptance coverage.
