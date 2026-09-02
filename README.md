# Synesthesia Machine

Synesthesia Machine is a desktop instrument for Windows and Linux that transforms video and
camera data into live MIDI note states through a typed visual node graph.

## Requirements and setup

- Windows 11 x64 or Linux x64 (glibc); see [ADR-0012](docs/adr/0012-windows-and-linux-platform-support.md)
- [`uv`](https://docs.astral.sh/uv/)
- No system Python is required; `uv` manages the supported CPython 3.12 runtime.

```powershell
uv python install 3.12
uv sync --locked
```

On a fresh Linux machine also install the Qt shared libraries used by the offscreen platform
(`libgl1`, `libegl1`, `libxkbcommon0`, `libfontconfig1`, `libglib2.0-0`) and, for the optional
debug-audio feature, `libportaudio2` (sounddevice loads PortAudio from the system on Linux).

The committed `.python-version` constrains commands to Python 3.12. The synchronized environment
lives in the ignored repository-local `.venv` directory.

## Development commands

The same `uv` commands run on Windows (PowerShell) and Linux (bash):

```bash
uv run synmachine
uv run synmachine --smoke-test
uv run check
uv run test
uv run python -m tools.validate_graphs examples tests/fixtures/compatibility
uv run python -m tools.synesthesia_benchmarks
uv run python -m tools.reference_benchmark
uv run python -m tools.recovery_probe
uv run python -m tools.large_graph_profile
```

See [`tools/README.md`](tools/README.md) before running hardware probes, long diagnostics, evidence
generators, or release commands. Automated tests use generated media and mock devices; they do not
require a camera, MIDI port, or audio output device.

## Examples

Open any graph under [`examples/library`](examples/library). Each graph resolves its bundled media
relative to its own location and is safe to inspect without hardware:

- [`hue-chord.synmachine.json`](examples/library/hue-chord.synmachine.json) demonstrates the full
  video-to-note-state path with visualization and disabled-by-default debug audio.
- [`reference-image-pipeline.synmachine.json`](examples/library/reference-image-pipeline.synmachine.json)
  exercises a representative image-processing pipeline and two preview branches.
- Motion Grid, Edge Ensemble, Scanning Score, and Spatial Spectrum demonstrate the visual-to-musical
  algorithms with deterministic input and safe sinks.

The exact current built-in registry is persisted at
[`examples/catalogue/current.synmachine.json`](examples/catalogue/current.synmachine.json). It is a
disconnected persistence/instantiation smoke graph, not an executable composition. Historical
catalogues used to verify compatibility live under [`tests/fixtures/compatibility`](tests/fixtures/compatibility),
not alongside current examples. See [`examples/README.md`](examples/README.md) for graph details and
[`docs/reference/node-reference.md`](docs/reference/node-reference.md) for algorithm behavior.

## Architecture

The active implementation contract is [`docs/architecture/master.md`](docs/architecture/master.md).
Accepted deviations are recorded under [`docs/adr`](docs/adr). Active repository structure follows
domain ownership rather than the chronology in which features were delivered:

- `src/synesthesia_machine/contracts`: immutable runtime values and versioned client/process messages;
- `graph`, `nodes`, `runtime`, and `persistence`: Qt-free authoring, compilation, execution, and storage;
- `media` and `midi`: reusable device-independent algorithms and mockable hardware services;
- `ui`: PySide6 editor projections and user interaction;
- `tests`: domain suites plus integration, benchmark, packaging, and smoke coverage;
- `examples`: current user-facing graphs, catalogue, and deterministic media;
- `benchmarks/fixtures` and `packaging/fixtures`: non-user benchmark and release inputs;
- `docs/history/v0-development`: preserved delivery packets, completion reports, and measured evidence.

The historical milestone sequence remains useful acceptance evidence, but it is not an active module,
test, example, or tooling boundary.
