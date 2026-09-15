# ADR 0020: Auto-resume playing sources after a broken-graph stop

- Status: Accepted
- Date: 2026-09-15

## Context

ADR 0013 makes a broken graph stop the engine: sources are stopped and closed, the worker is
drained, MIDI output panics, and the client reports `EngineState.STOPPED`. When the user fixes
the document, the debounced activation starts the engine again. But the freshly created sources
come back in the ready state: playback that was running before the stop never resumes. For a
live instrument that is a visible and audible gap — the user is still editing, expects the
performance to continue, and today has to press play again for every source.

The play state is lost because the stop transition tears down the source controllers that hold
it, and nothing records which sources were playing. The memory belongs to the engine client:
ADR 0013 already places ownership of the stop transition in the client, and the child engine
process hosts the same client class, so one implementation covers the in-process and process
transports identically. The UI has no per-source transport state of its own and its single-target
transport commands cannot re-express multi-source play state, so it cannot supply the memory.

## Decision

The engine client remembers which sources were in the play family (`PLAYING` or
`RECONNECTING`) when a broken-graph stop tears the runtime down. The next *successful*
activation replays exactly those source nodes:

- the memory is captured in the stop transition, before the sources are stopped and closed, and
  only while live sources exist — a second consecutive broken activation (no sources left) must
  not wipe a memory captured by an earlier one;
- on the next valid activation, sources whose node IDs were remembered are started with
  `play()` (fresh sources are in the ready state); a source that fails to start is skipped so one
  bad source cannot block the healthy ones, and its error still surfaces through the normal
  status and metrics paths;
- the memory is consumed (cleared) by the successful activation that used it, and is also
  cleared by `close()`;
- only the play family is replayed: sources that were paused, stopped, or ended come back
  ready and are never auto-played;
- matching is by source node ID (stable across graph revisions while the node persists); a
  deleted-and-re-added source (new ID) does not auto-play;
- play position is **not** preserved: sources are torn down by the stop, so resumed sources
  start from the beginning of playback — the same position loss a fresh `play()` has today.

No engine protocol, graph schema, or `ENGINE_PROTOCOL_VERSION` change: the memory is internal
to the client. A child-process crash followed by `restart()` re-activates the last valid
snapshot in a fresh child process with no memory, so no auto-play happens across a crash;
`restart()` semantics are otherwise unchanged (ADR 0013). The UI is unchanged: its periodic
status refresh already surfaces the resumed `RUNNING` state within the existing 100 ms cadence.

## Consequences

- A performance interrupted by a broken graph continues playing after the user fixes the
  graph, without an extra play gesture; the trade-off accepted in ADR 0013 (never run output
  the document does not describe) now includes resumption of the output it did describe.
- Transient mid-edit stops can now briefly stop and restart playback; resumed sources restart
  from the beginning of the video, which is a visible (and, with audio, audible) consequence of
  the teardown that ADR 0013 already required.
- A source that was playing but whose node was deleted before the graph became valid again is
  not replayed; only source nodes present in the new plan are started.
- Tests must cover: in-place fix after a broken stop (playing source auto-resumes), paused
  sources are not auto-played, no matching sources clears the memory, and a second consecutive
  broken activation preserves the captured memory.

## References

- `docs/adr/0013-engine-auto-stops-on-broken-graph.md`
- `docs/architecture/master.md` (section 9.5)
