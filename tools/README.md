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

Automated tests never access physical devices. Camera behavior is tested through injected capture
factories, MIDI through `MockMidiBackend`, audio by calling the callback with preallocated arrays,
and PyAV through a generated temporary MP4.