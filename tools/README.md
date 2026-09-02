# Diagnostics and evidence tools

Run commands from the repository root through `uv` on Windows (PowerShell) or Linux (bash). The
command lines below are identical on both shells. Enumeration-only commands are safe without
hardware. Commands that open a camera or output device are explicitly opt-in.

## Environment and device probes

```powershell
uv run python -m tools.generate_test_video diagnostics/tiny.mp4
uv run python -m tools.pyav_probe diagnostics/tiny.mp4
uv run python -m tools.camera_probe --start 0 --stop 5
uv run python -m tools.midi_probe --list
uv run python -m tools.audio_probe --play
uv run python -m tools.process_ipc_probe
uv run python -m tools.environment_report --output packaging/environment-report.json
```

`camera_probe` opens the selected indexes briefly. `audio_probe --play` sends a quiet tone to the
default output. The other commands above do not select or write to physical outputs.

## UI and runtime diagnostics

```powershell
uv run python -m tools.ui_diagnostic
uv run python -m tools.process_ui_diagnostic
uv run python -m tools.reference_benchmark
uv run python -m tools.profiler_overhead
uv run python -m tools.large_graph_profile
```

These commands launch Qt and write JSON, and the UI diagnostics also write screenshots. Default
outputs live under `docs/evidence`. Benchmark results are machine-specific; follow architecture
section 18.6 before using them as release evidence.

## Explicit MIDI hardware evidence

First enumerate outputs with `tools.midi_probe --list`. To send evidence, both port arguments must
match the same exact enumerated name:

```powershell
uv run python -m tools.midi_hardware_evidence `
  --port 'EXACT ENUMERATED PORT NAME' `
  --confirm-exact-port 'EXACT ENUMERATED PORT NAME' `
  --hold-seconds 1
```

The command opens only that output, sends low-velocity C4 for one second, sends note-off, panics with
CC123, closes, and then writes `docs/evidence/midi-hardware.json`. It cannot prove what a separate
monitor displayed; record that as a distinct manual observation.

## Deterministic benchmarks and soaks

```powershell
uv run python -m tools.hold_image_soak
uv run python -m tools.synesthesia_benchmarks
uv run python -m tools.runtime_soak
```

The Hold Image soak executes 108,000 scheduler ticks without sleeping, equivalent to 30 minutes at
60 FPS. It verifies exact retained capacity, reset cleanup, scheduler errors, and bounded process RSS.
The synesthesia benchmark times deterministic 500×500 algorithm fixtures and verifies stable note
state. The runtime soak combines memory, profiling, and mock-MIDI overload checks. These tools do not
open physical devices.

## Persistence, recovery, and release

```powershell
uv run python -m tools.validate_graphs examples tests/fixtures/compatibility
uv run python -m tools.recovery_probe --output docs/evidence/recovery.json
uv run python -m tools.release check
```

The validator is read-only unless `--output` is supplied. The recovery probe intentionally terminates
a child with exit code 73 after durable autosave replacement. Release commands may inventory native
dependencies, create provenance, or archive a prepared artifact; read `packaging/README.md` before
using any command beyond `check`.
