# Synesthesia Machine

Synesthesia Machine is a Windows desktop instrument that transforms video and camera data into
live MIDI note states through a typed visual node graph.

The project is currently implementing **Phase 0: foundation and risk spikes**. Graph and node
features are intentionally out of scope until the external Windows boundaries are proven.

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
uv run synmachine          # Open the minimal Qt application
uv run synmachine --smoke-test
uv run check               # Ruff format/lint and strict Pyright
uv run test                # Phase 0 smoke tests
```

Hardware spikes are documented in [`tools/README.md`](tools/README.md). Automated tests use
generated media and mocks; they never require a camera, MIDI port, or audio output device.

## Architecture

The implementation contract is
[`synesthesia_machine_design/00_master_architecture.md`](synesthesia_machine_design/00_master_architecture.md).
Any deviation from that baseline requires an Architecture Decision Record under [`docs/adr`](docs/adr).

Phase 0 code is deliberately narrow:

- `src/synesthesia_machine/app`: application bootstrap and infrastructure;
- `src/synesthesia_machine/contracts/engine_messages.py`: versioned IPC proof contracts;
- `tools`: isolated external-boundary spikes;
- `tests/smoke`: hardware-independent acceptance tests.