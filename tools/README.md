# Phase 0 risk spikes

Run every command from the repository root through `uv`. Read-only enumeration commands are safe
without hardware; commands that open a camera or output device are explicitly opt-in.

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
```

Automated tests never access physical devices. Camera behavior is tested through injected capture
factories, MIDI through `MockMidiBackend`, audio by calling the callback with preallocated arrays,
and PyAV through a generated temporary MP4.