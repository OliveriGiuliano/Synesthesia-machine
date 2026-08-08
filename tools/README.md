# Diagnostics and acceptance tools

Run every command from the repository root through `uv`. Read-only enumeration commands are safe
without hardware; commands that open a camera or output device are explicitly opt-in.

## Phase 0 risk spikes

```powershell
# Generate and inspect a deterministic tiny video
uv run python -m tools.generate_test_video diagnostics/tiny.mp4
uv run python -m tools.pyav_probe diagnostics/tiny.mp4

# Probe camera indexes 0..4 on a worker thread (opens devices briefly)
uv run python -m tools.camera_probe --start 0 --stop 5

# Enumerate real MIDI outputs without selecting or sending to one
uv run python -m tools.midi_probe --list

# Send a one-second quiet A4 tone to the default audio device
uv run python -m tools.audio_probe --play

# Verify Windows spawn, versioned ping/pong, and shared RGB bytes
uv run python -m tools.process_ipc_probe

# Write the dependency/system report required by Phase 0
uv run python -m tools.environment_report --output packaging/environment-report.json

# Run the canonical Phase 3 graph in the UI for 60 seconds and capture evidence
uv run python -m tools.phase3_performance
```

## Phase 4 acceptance evidence

```powershell
# Run the canonical Hue Chord graph through ProcessEngineClient/EngineServer.
# Uses a 5-second warm-up and a 30-second process-backed measurement, then writes
# docs/phase-4-performance.json and docs/phase-4-ui.png. Debug audio stays disabled.
uv run python -m tools.phase4_performance

# Enumerate MIDI outputs without opening or sending to any port.
uv run python -m tools.midi_probe --list

# EXPLICITLY OPT-IN HARDWARE ACTION: open only one exact enumerated output, send
# low-velocity C4 for one second, send explicit note-off, panic with CC123, and close.
# Both port arguments must match exactly; unavailable names are refused without substitution.
uv run python -m tools.phase4_midi_evidence `
  --port 'EXACT ENUMERATED PORT NAME' `
  --confirm-exact-port 'EXACT ENUMERATED PORT NAME' `
  --hold-seconds 1
```

The MIDI command writes `docs/phase-4-midi-evidence.json` only after the production
`MidiOutputService` reaches `CLOSED`. It cannot verify what a separate MIDI monitor displayed, so
visual receipt must be recorded as a distinct manual observation rather than inferred from a
successful send lifecycle.

## Phase 5 bounded-memory evidence

```powershell
# Execute 108,000 production Scheduler ticks without sleeping: 30 minutes of source-clock
# duration at 60 FPS. The command writes docs/phase-5-soak.json and exits non-zero if a gate fails.
uv run python -m tools.phase5_soak
```

The tool production-compiles Load Video → Hold Image → Display Image Data, injects deterministic
immutable 64×64 RGB float32 source frames, and simulates a source-loop reset every 600 ticks. It does
not open the video decoder or any physical device. The pass criteria are:

- no scheduler errors;
- Hold Image reaches but never exceeds its exact eight-frame capacity;
- every simulated source-loop reset releases retained frame references; and
- post-warm-up sampled process-RSS growth and span each remain at or below 32 MiB.

Exact Hold Image capacity is checked immediately before every loop reset. RSS is sampled at comparable
post-reset lifecycle points, so the growth/span comparison detects memory that remains across loops
rather than the expected bounded history. RSS is intentionally the sole process-memory criterion and
includes Python allocator and native-library behavior visible to the OS, so the gate uses a documented
allowance rather than exact equality. This is accelerated source-clock evidence, not 30 minutes of
wall-clock operation. Use CLI overrides such as `--ticks`, `--sample-interval`, `--width`, and
`--height` only for local diagnostics; the no-argument command is canonical.

## Phase 6 algorithm microbenchmarks

```powershell
# Run Scanline, Edges to Pitch, Fourier, and Optical Flow on deterministic 500×500 fixtures.
# The canonical command uses three warm-ups and ten measured invocations per algorithm.
uv run python -m tools.phase6_benchmarks
```

The command writes `docs/phase-6-benchmarks.json` and exits non-zero unless every invocation returns
the same non-empty MIDI note state with finite non-negative timing. It records fixture SHA-256 values,
all timing samples, median/p95, environment/dependency versions, and Git state. Fourier reuses one
shape cache across the complete run. Use `--warmup-runs` and `--measured-runs` for fast local checks;
the input resolution remains fixed at the required 500×500.

This tool times direct Qt-free algorithms over pre-built immutable fixtures. It excludes fixture
construction, JSON serialization, decoder, scheduler, process transport, UI, audio, and MIDI hardware
costs. It is intentionally distinct from the architecture section 18.6 full-graph release methodology
and does not claim source/processed FPS, drops, end-to-end latency, a 30-second run, or a three-run
release median. Deterministic output state is the gate; wall-clock equality is not.

## Phase 7 robustness and editor evidence

```powershell
# Spawn a child that writes an autosave and exits immediately with code 73. The parent proves the
# unsaved node and explicit graph path are recoverable, then writes docs/phase-7-recovery.json.
uv run python -m tools.phase7_recovery --output docs/phase-7-recovery.json

# Validate current examples or legacy fixtures through graph and node migration chains. The command
# is read-only and exits non-zero if any graph is invalid.
uv run python -m tools.phase7_validate_graphs examples tests/fixtures/phase7

# Build a 500-node graph in the real offscreen QGraphicsScene, edit one node, select one lazy editor,
# enter low-detail mode, and write docs/phase-7-large-graph.json.
uv run python -m tools.phase7_large_graph
```

The recovery harness intentionally uses `os._exit(73)` after the production `AutosaveStore` has
durably replaced both recovery payload and manifest. It does not simulate an operating-system power
loss during an individual filesystem flush.

The graph validator never rewrites inputs. It reports source/current graph versions and the count of
required node-version steps. The committed legacy fixtures cover graph v0→v1 plus Number v0→v1 and
Load Video v0→v1 parameter migrations.

The large-graph gate requires scene construction at or below 5000 ms, one incremental parameter edit
at or below 1000 ms, no eager parameter editors for the full graph, stable identity for unaffected
graphics items, and no visible ports/editors below 0.55 zoom. These are local offscreen interaction
criteria, not GPU/display or runtime-throughput benchmarks.

Automated tests never access physical devices. Camera behavior is tested through injected capture
factories, MIDI through `MockMidiBackend`, audio by calling the callback with preallocated arrays,
and PyAV through a generated temporary MP4.
