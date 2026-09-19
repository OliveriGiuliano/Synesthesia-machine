# 09: Publish the resolved loop region

**What to build:** The played region has no owner. The same invariants (0.0 end sentinel, start < end, clamp to duration) are derived in five places with corner rules that diverge: the node validator (`_validate_load_video`) rejects `start >= end` only when `end > 0`; the service constructor independently sets a region error whenever `loop_start_s >= duration` regardless of the loop flag or `loop_end_s` — so a stale persisted start bricks even a `loop=False` source into permanent `ERROR` with a silently no-op `play()`; `reload()` re-derives only the error and never the clamped region or the head frame budget, leaving them stale against the new file's duration despite its own "a longer file may make the previously empty region playable again" comment; the editor resolver re-derives bounds per projection from `SourceStatus.duration_s`; and two UI editors (`TimestampParameterEditor`, `LoopRangeParameterEditor`) re-implement the sibling rules in position space. The resolved region is absent from `SourceStatus` (which carries duration and position but not the region), so tests reach into private attributes (`source._region_start_s`) and the UI cannot render or constrain against the region the engine actually plays.

**Solution:** Extend ADR-0023's "editors take their bounds from engine-published status" from duration to the region: the source service resolves the region once (parameters + probed duration, including the loop-flag and sentinel rules) and publishes the resolved start/end and the region error state in `SourceStatus`; the validator, the editor resolver, and the playback controls read the published facts instead of re-deriving them; `reload()` re-publishes a region consistent with the new duration. A stale persisted start then degrades gracefully everywhere — the published region says so — instead of one owner hard-erroring while the others stay legal.

**Status:** needs-triage

**Blocked by:** 06, 07

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/contracts/engine_client.py` (`SourceStatus` fields; protocol 16→17)
- `src/synesthesia_machine/nodes/input/video.py` (validator, editor resolver)
- `src/synesthesia_machine/ui/parameter_editors.py`
- `src/synesthesia_machine/ui/playback_controls.py`
- `docs/adr/` (new ADR or ADR-0023 addendum, before implementation)

**Acceptance:**
- [ ] `SourceStatus` carries the resolved region (start, end, error state); the protocol bump and handshake mismatch are explicit
- [ ] A stale persisted start at or past the duration no longer hard-errors a loop-off source; the published region describes the fallback
- [ ] `reload()` publishes a region and head budget consistent with the reloaded file's duration
- [ ] Region tests assert published status, not private attributes
- [ ] ADR written before implementation; protocol-version consistency test updated
- [ ] `uv run check` green; full media + node + UI test suites green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (video-source scout F3). ADR gate: `SourceStatus` crosses the wire, so this is a protocol change.
