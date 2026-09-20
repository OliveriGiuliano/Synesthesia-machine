# ADR 0028: The engine publishes the resolved loop region

- Status: Accepted
- Date: 2026-09-20
- Context: ADR-0021 (video source seek and loop segments), ADR-0023 (video editor bounds
  from engine status), ADR-0024 (pre-decoded loop head), ADR-0025 (run-to-end simulation
  observation), ADR-0027 (bump-only protocol changes)

## Context

ADR-0021 moved loop-region authority to the engine: the `Load Video` source resolves the
played region once at construction (sentinel rule for `loop_end_s == 0.0`, clamping, the
`[0, end)` fallback) and reports `duration_s`, `region_end_s`, and `total_index` through
engine-published `SourceStatus`. ADR-0023 made the UI timestamp editors read those
published facts. But the region still had no single published owner of its full resolved
form:

- The **resolved start** was never published. `SourceStatus` carried `region_end_s` but no
  `region_start_s`, so every consumer that needed the played interval re-derived half of
  it: playback controls scaled to `[0, duration]` while the engine clamped seeks to
  `[region_start, region_end]`; the UI and the engine maintained two independent bounds
  rules that only converged on the next telemetry tick.
- A **stale start** (a persisted `loop_start_s` at or past the video's end, e.g. after a
  shorter file was reloaded) was a hard configuration error in the source service —
  regardless of the loop flag — even though the existing fallback rule already resolves
  such a configuration to a playable region. The state machine folded the configuration
  error into `SourceState.ERROR` and made `play()` a **silent no-op**, so a user with one
  out-of-range timestamp in a graph with several sources could not play anything, while
  every other owner (node validator, editor resolver, loop editors) reported the same
  configuration as legal. The two error kinds (configuration vs. runtime failure) were
  entangled in a single `state=ERROR + last_error` fact.
- `reload()` re-derived only the region *error*, not the resolved region, `total_index`,
  or the ADR-0024 head budget, so a reloaded file briefly published a region that
  belonged to the old file.
- `SourceStatus` carried no distinct region-error **fact**; a region problem was
  indistinguishable in the published state from a decode or file failure.

## Decision

- `VideoSourceService` remains the single owner of region resolution. The resolution
  (sentinel, clamping, fallback) plus the facts derived from it (`total_index`, ADR-0024
  head budget) is computed once per (configuration, probed metadata) by one shared
  resolution step invoked from both the constructor and `reload()`, so a reloaded file
  always publishes a region consistent with its own duration.
- **Configuration is never fatal.** The fallback rule guarantees a playable region for
  every finite configuration; when a start timestamp the file cannot honour is resolved,
  the service publishes that as a **region error fact** — a message describing the
  fallback — without entering `SourceState.ERROR`. `SourceState.ERROR` now means exactly
  one thing: a runtime (file/decode/device) failure. `play()` therefore never no-ops on
  a configuration problem: it plays the published fallback region, and `ERROR` always
  requires a reload.
- `SourceStatus` gains two published facts next to the existing `region_end_s`:
  `region_start_s` (the resolved start; `None` when the container reports no duration or
  for camera sources) and `region_error` (the fallback message; `None` when the
  configuration is honoured as-is). The protocol version is bumped 18 -> 19; per
  ADR-0027 the handshake guard is the only compatibility mechanism, so both sides of the
  boundary must ship with the change.
- Consumers read the published facts instead of re-deriving them: playback controls
  scale and nudge within the resolved region (start to end, falling back to duration
  when the end is unknown); the loop timestamp editors keep the published *duration* as
  their bound, because they are the configuration inputs that define the region; the
  node validator keeps only the `start < end` ordering rule, because it is a
  parameters-only hook with no status access (ADR-0023 keeps definition validation
  I/O-free) and duration-dependent legality is now the engine's published region fact
  rather than an up-front rejection.
- The UI's editor re-projection digest includes the region facts, so a reload that
  changes the region re-projects editor and playback bounds.

## Consequences

- A graph with a stale `loop_start_s` (loop on or off) plays again: the source runs the
  fallback region and publishes `region_error` describing it, instead of bricking the
  whole engine with a silent no-op. This also restores ADR-0009/ADR-0013's "one pass
  from start to end" offline MIDI export for such graphs: the export simulates the
  fallback region to its end instead of failing on a source that was stuck in
  `ERROR`.
- The engine-level state fold (`_effective_state`) no longer folds configuration
  problems into `EngineState.ERROR`; configuration problems surface through the region
  facts, runtime failures through `last_error`/`ERROR` as before.
- The `SourceState.ERROR` contract is stricter and simpler; `play()` from `ERROR` always
  raises "must be reloaded" because the only way to enter `ERROR` is a runtime failure.
- Camera sources and failed sources publish `region_start_s`/`region_error` as `None`
  (new defaulted fields), so their status payloads are unchanged in shape.
- Any consumer that treats `SourceState.ERROR` as "possibly a configuration problem"
  must switch to reading the region facts; such consumers are, after this ADR, only the
  status-presenting surfaces.

## References

- ADR-0009, ADR-0013: offline MIDI export of looping videos, one pass to the region end.
- ADR-0021: engine-side seek and loop-segment authority.
- ADR-0023: editor bounds from engine-published facts; validators stay I/O-free.
- ADR-0024: pre-decoded loop head (head budget is a fact derived from the resolved
  region and must be re-resolved on reload).
- ADR-0025: run-to-end observation; `total_index`/`region_end_s` already published.
- ADR-0027: bump-only protocol changes and the handshake guard as the compatibility
  mechanism.
