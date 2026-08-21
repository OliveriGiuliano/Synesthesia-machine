# Phase 0 — Foundation and risk spikes

## Objective

Establish a reproducible Windows 11 x64 project and prove every high-risk external boundary before application architecture is built on it.

## Out of scope

No graph model, node editor, production algorithms, final theming, or installer.

## Fixed decisions

- CPython 3.12 x64.
- `uv`, `pyproject.toml`, committed `uv.lock`.
- PySide6/Qt Widgets.
- NumPy and OpenCV 4.13.x; do not adopt OpenCV 5 during this phase.
- PyAV 18.x for file decoding.
- Mido + python-rtmidi for MIDI ports.
- sounddevice for debug audio.
- psutil for system metrics.
- Windows multiprocessing uses `spawn`.

## Allowed paths

Repository root, `src/synesthesia_machine/app/`, `src/synesthesia_machine/contracts/engine_messages.py`, `tools/`, `tests/smoke/`, `docs/adr/`, and `packaging/` skeleton.

## Ordered tasks

1. Create the src-layout project, console entry point, version module, Ruff/Pyright/pytest configuration, and commands `uv run synmachine`, `uv run test`, `uv run check`.
2. Create a minimal `QMainWindow` that opens and exits cleanly.
3. Add structured rotating logging with session ID and separate UI/engine logger names.
4. Write a PyAV spike that opens a chosen video, prints stream metadata, decodes frames, reads presentation timestamps, converts one frame to RGB NumPy, and exits without leaked handles.
5. Write an OpenCV camera probe that enumerates a configurable index range using Media Foundation then DirectShow fallback. It must not run on the UI thread.
6. Create a MIDI abstraction with a mock backend. Prove real port enumeration through Mido/python-rtmidi when available, but tests must use the mock.
7. Produce a 440 Hz sine through sounddevice for one second using a callback that performs no allocation or logging.
8. Spawn an engine child process. Exchange versioned ping/pong dataclasses. Create a shared-memory RGB slot, have the child write a generated frame, and have the parent verify its contents. Cleanup must be idempotent.
9. Add a small psutil report.
10. Create ADRs for language, UI toolkit, media stack, MIDI stack, process separation, and dependency management.

## Required tests

- application bootstrap import test;
- child process start/stop and forced-termination cleanup;
- shared-memory contents and cleanup;
- mock MIDI enumeration/send;
- PyAV decode of a generated tiny test video;
- logging file creation;
- no physical device required for automated tests.

## Exit criteria

- `uv sync --locked`, `uv run check`, and `uv run test` succeed on Windows 11 x64.
- The UI process and child process always terminate.
- One video frame crosses shared memory without pickling the array.
- Dependency versions are recorded in a generated environment report.
- No code assumes Python 3.14.

## Completion report

Implemented; public interfaces; tests/results; known limitations; ADRs; recommended next work.
