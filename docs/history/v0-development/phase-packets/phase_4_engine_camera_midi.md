# Phase 4 — Production engine process, camera, and MIDI output

## Objective

Move runtime execution behind a supervised child process, add live camera input, and safely send MIDI to real Windows ports.

## Process contract

- UI owns Qt and GraphDocument.
- Engine owns runtime instances, source handles, frames, node execution, MIDI output service, and profiling.
- Commands/events are versioned dataclasses with graph revision IDs.
- Full float32 frames never pass through queues.
- Previews use bounded shared memory and sequence numbers.
- Structural graph update compiles before atomic swap; invalid new snapshots do not replace the last valid plan.

## Backpressure contract

Each source has at most two pending frames. A new live frame replaces the stale pending frame. Drops are counted. Latency must not grow without bound.

## MIDI contract

- Mido + python-rtmidi backend behind a testable service.
- Synesthesia values are desired note states.
- service diffs state; note-offs precede note-ons;
- velocity policies: Ignore while held, Retrigger, Repeat note-on;
- explicit note-offs plus CC123 panic on stop/delete/port switch/shutdown/error;
- missing port is an unavailable state, never an arbitrary auto-substitution.

## Allowed paths

`runtime/engine_client.py`, `engine_server.py`, IPC/shared preview/lifecycle, `media/camera_source.py`, `midi/output_service.py`, Send MIDI node, process supervision UI/status, integration tests.

## Ordered tasks

1. Define protocol version and command/event dataclasses.
2. Implement child bootstrap, graceful close, forced close, and heartbeats.
3. Implement graph snapshot load, compile, live parameter update, structural plan swap, and runtime preservation rules.
4. Implement shared-memory preview manager with crash-safe cleanup.
5. Implement bounded source mailboxes and metrics.
6. Implement camera enumeration/open/capture/reconnect with Media Foundation and DirectShow fallback.
7. Implement MIDI backend interface, real RtMidi adapter, mock adapter, output service, Send MIDI node, and global panic.
8. Implement engine crash UX and restart.

## Required tests

- process start/stop/restart and protocol mismatch;
- invalid snapshot leaves old plan active;
- shared-memory resize and both-side crash cleanup;
- deliberately slow node causes drops but bounded latency;
- simulated camera disconnect/reconnect;
- full MIDI safety matrix with mock backend;
- engine kill leaves UI usable and recoverable.

## Exit criteria

Live camera graphs run in the engine process; real or loopback MIDI ports can be selected; the UI remains responsive under load; lifecycle transitions do not leave tracked notes active.

## Completion report

Implemented; protocol/interfaces; tests/results; manual hardware checks; performance; limitations; deviations; next work.
