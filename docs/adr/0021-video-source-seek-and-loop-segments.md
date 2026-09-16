# ADR 0021: Video source seek and loop segments

- Status: Accepted
- Date: 2026-09-16

## Context

The master architecture (§11.1) listed Seek as a command the source API must reserve
but never implement: the inspector offered no way to move through a video, and a
looping source could only play the whole file. Two editor features close that gap:

1. A loop region on the Load Video node: the user picks start and end timestamps
   (sliders plus timestamp fields, constrained so start ≤ end and both stay inside
   the file) and the source replays only that segment.
2. Playback controls in the inspector: a progress slider driven by source status
   telemetry, plus rewind/forward nudge buttons (±5 s, ±15 s) that seek the source.

Both need engine-side support that the v14 protocol did not provide: there was no
transport command for seeking, and `SourceStatus` carried no playback position, so
the UI could neither issue a seek nor render a progress slider.

## Decision

- The engine protocol is bumped from v14 to v15:

  - `TransportAction.SEEK` sends `(source_node_id, source_time_s)` to the engine.
  - `SourceStatus` gains `source_time_s: float | None`: the source's current
    playback position in seconds, or `None` for sources that do not report one.
    The field is part of the existing periodic status telemetry, so no new event
    type is needed.

- `VideoSourceService` implements `seek(source_time_s)`: the target is clamped to
  the played segment (the loop region when looping, otherwise the whole file),
  recorded immediately for status reporting, and handed to the decode thread via a
  seek event plus a generation counter, so the call never blocks on a full decode
  queue and the presentation thread only observes the result. A seek issued while
  stopped records the position for the next play. Non-seekable sources (camera,
  failed preparation) reject the command.

- The Load Video node moves to implementation version v2 with two new parameters:
  `loop_start_s` and `loop_end_s` (seconds, default 0.0, `RESTART_SOURCE` updates,
  timestamp editors in the inspector). 0.0 means "video start" / "video end".
  While looping, playback is confined to `[loop_start_s, loop_end_s)`; an empty or
  inverted segment falls back to a valid one so the source always has something
  to play. The v1-to-v2 migration back-fills the 0.0 defaults, and the node
  validator rejects a start that is not below the end.

- The inspector shows the playback controls for Load Video nodes only. Engine
  telemetry updates the slider; telemetry must not move a slider the user is
  currently holding down.

## Consequences

- A v14 client and a v15 engine mismatch at the handshake, so mixed-version
  process pairs refuse to run rather than silently drop the new fields — the
  existing handshake protection covers the change without extra work.
- Looping sources now play a segment instead of the whole file; saved graphs from
  before the change migrate to v2 with a full-file segment, so existing projects
  keep their old behaviour.
- The generated catalogue and example graphs were regenerated for v2, and the
  node reference, master architecture (§8.5, §11.1), and protocol-version
  consistency test were updated in the same change.
- The in-process engine forwards `seek` directly to the source service; the
  process transport carries it as a transport command, so both transports share
  one engine-side implementation.
