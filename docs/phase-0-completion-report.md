# Phase 0 completion report

## Status

Phase 0 — Foundation and risk spikes is complete on Windows 11 x64 using a
project-local, uv-managed CPython 3.12 environment.

## Implemented

- Created a reproducible src-layout Python project with `pyproject.toml`, a committed
  `uv.lock`, `.python-version`, and stable `synmachine`, `check`, and `test` commands.
- Added a minimal PySide6/Qt Widgets `QMainWindow` that supports automated clean-exit
  smoke testing.
- Added rotating JSON Lines logging with session/process context and separate UI and
  engine logger names.
- Added a deterministic PyAV video generator and a decode probe that reports stream
  metadata, frame PTS/time, and RGB NumPy conversion.
- Added an off-UI-thread OpenCV camera probe with Media Foundation to DirectShow
  fallback and deterministic capture cleanup.
- Added a mockable MIDI boundary plus optional Mido/python-rtmidi output enumeration.
- Added a preallocated sounddevice sine callback whose callback path does not log,
  block, or allocate arrays.
- Added Windows `spawn` ping/pong IPC using versioned immutable dataclasses and a
  parent-owned shared-memory RGB slot. Image bytes do not pass through the pipe.
- Added graceful and forced child-process termination with idempotent pipe, process,
  and shared-memory cleanup.
- Added a psutil-based environment reporter and generated
  `packaging/environment-report.json` with exact interpreter and dependency versions.
- Added device-independent smoke coverage for the application, logging, media,
  camera, MIDI, audio callback, environment report, process lifecycle, and shared
  memory.

## Public interfaces added or changed

- Console commands:
  - `uv run synmachine [--smoke-test]`
  - `uv run check`
  - `uv run test`
- Application foundation:
  - `synesthesia_machine.app.application.MainWindow`
  - `synesthesia_machine.app.application.create_application`
  - `synesthesia_machine.app.settings.ApplicationPaths`
  - `synesthesia_machine.app.logging_setup.configure_logging`
- Versioned engine contracts:
  - `ENGINE_PROTOCOL_VERSION`
  - `Ping`, `Pong`, `WriteSharedFrame`, `SharedFrameReady`, `Shutdown`, and
    `ShutdownAcknowledged`
- Phase 0 probes under `tools/`:
  - generated video and PyAV decode;
  - camera backend fallback;
  - mock and real MIDI boundaries;
  - preallocated audio callback;
  - spawn process and shared-memory lifecycle;
  - environment report generation.

These probe interfaces prove external boundaries. They are not yet the production
engine or domain API and may be promoted or replaced in later phases behind stable
application interfaces.

## Tests and results

Final acceptance was run on Windows 11 x64 with CPython 3.12.13:

| Command | Result |
| --- | --- |
| `uv sync --locked` | Passed; 24 packages resolved and checked from the lockfile. |
| `uv run check` | Passed; 27 files formatted, Ruff clean, Pyright strict with 0 errors/warnings. |
| `uv run test` | Passed; 10 tests in 0.48 seconds. |
| `uv run synmachine --smoke-test` with `QT_QPA_PLATFORM=offscreen` | Passed; UI started and stopped cleanly. |
| Generated-video + PyAV probe | Passed; MPEG-4, 64×48, 6 frames at 12 fps, first PTS/time 0/0.0, RGB `uint8`. |
| Spawn/shared-memory probe | Passed; child acknowledged ping/frame, checksum 118272, process terminated. |

Automated tests open no physical camera, MIDI, or audio device. Camera behavior uses
fake capture handles, MIDI uses the mock backend, and audio invokes the callback with
preallocated NumPy output.

## Known limitations

- Real camera capture, MIDI hardware enumeration, and audio playback are opt-in manual
  probes and were not required for automated acceptance on this machine.
- The offscreen Qt platform plugin reports missing bundled fonts and unsupported
  `propagateSizeHints()`. The smoke run still starts and stops normally; the native
  Windows Qt platform uses system fonts.
- The shared-memory transport is a single generated RGB slot for boundary validation,
  not the production preview-slot protocol or engine scheduler.
- The sounddevice spike proves callback discipline for a fixed two-channel, 256-frame
  setup; production device selection, status handling, and lifecycle belong to a later
  phase.
- The camera probe verifies fallback and cleanup but does not define production camera
  ownership or reconnection behavior.
- No installer or bundled runtime is included in Phase 0.

## ADRs

- [ADR 0001 — CPython 3.12](adr/0001-python-312.md)
- [ADR 0002 — PySide6 and Qt Widgets](adr/0002-pyside6-qt-widgets.md)
- [ADR 0003 — Media stack](adr/0003-media-stack.md)
- [ADR 0004 — MIDI stack](adr/0004-midi-stack.md)
- [ADR 0005 — UI/engine process separation](adr/0005-ui-engine-process-separation.md)
- [ADR 0006 — uv dependency management](adr/0006-uv-dependency-management.md)

No architecture deviations were required during Phase 0.

## Recommended next work

Proceed to Phase 1 — Graph core:

1. Implement the framework-independent typed graph model and public interfaces before
   UI integration.
2. Add deterministic graph validation, mutation commands, and serialization primitives
   within the Phase 1 allowed paths.
3. Keep Qt, OpenCV, PyAV, MIDI, and sounddevice out of the graph domain layer.
4. Preserve versioned contracts and the locked quality gates established here.
5. Add focused tests for every graph behavior change and checkpoint meaningful
   milestones in Git.