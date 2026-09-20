# T08 research: one pass-state reset, one state writer

Research for `.scratch/architecture-deepening-3/issues/08-single-pass-state-reset.md`.
Verified against the committed code (head includes 9e989f6, ticket 07). All
citations are `file:line`; `vs.py` = `src/synesthesia_machine/media/video_source.py`,
`eb.py` = `src/synesthesia_machine/runtime/engine_body.py`,
`ipe.py` = `src/synesthesia_machine/runtime/in_process_engine.py`,
`es.py` = `src/synesthesia_machine/runtime/engine_server.py`.
"Verified" = read in code; "inferred" = derived by trace.

## 1. Per-pass state inventory

State owned by `VideoSourceService` (`vs.py`), with the paths that reset it:
`SP` = `_start_pass` (409–484), `HALT` = `_halt` (590–609), `PAUSE/RESUME`,
`AS` = `_apply_seek` (941–960), `RELOAD` (516–543; goes through HALT first).

| State | Set in `__init__` | SP | HALT | PAUSE/RESUME | AS | RELOAD |
|---|---|---|---|---|---|---|
| `_decode_queue` contents (327) | — | cleared 423 | cleared 594 | no | cleared (949) | via HALT |
| `_timeline` (337) | fresh | reset 427 | reset 601 | pause/resume shift anchor (489/499) | reset (955) | via HALT |
| `_source_frame_index` (348) | None | None 429 | None 602 | no | None (956) | via HALT |
| `_processed_index` (349) | 0 | 0 428 | 0 603 | no | 0 (957) | via HALT |
| `_pass_start_s` (325) | region_start | set 438–446 | **not touched** | no | no | no |
| `_position_s` (353) | 0.0 | set 447 | region_start only if STOPPED (607–609) | no | set by `seek()` (583) | via HALT |
| `_last_error` (352) | region_error | None 430 | **not cleared** | no | no | set/cleared 539–543 |
| `_seek_filter_pts` (356) | None | None in both branches (441/446) | None 606 | no | set by `seek()` (584); consumed by `_passes_seek_filter` (962–969) | via HALT |
| `_seek_event` (355) | unset | cleared if set (435) | cleared 605 | observed by resume (497) | claimed by presentation (971–983) | via HALT |
| `_seek_generation`/`_applied_seek_generation` (357–358) | 0/0 | applied synced (453) | **not touched** (next SP re-syncs) | no | applied by `_claim_pending_seek` | via HALT |
| `_head_in_use` (335) | False | **NOT reset** | False 596 | no | False (958) | via HALT |
| `_head_free` (341–342) | set | **NOT reset** | set 598 | no | set (959) | via HALT |
| `_head_trigger` (336) | clear | **re-armed, not cleared first** (set 483, only if a head thread spawns) | clear 597 | no | no | via HALT |
| `_head_queue` (332) | empty Queue(budget) | **NOT replaced** | replaced 599 | no | no | via HALT |
| `_head_container` (334) | None | kept open (ADR-0024) | released via `_release_containers` (595 → 611–624) | no | no (ADR-0024: "A seek does not clear the buffer") | released |
| `_live_container` (346) | None | kept open | released (623) | no | no | released |
| worker threads (338–340) | None | joined then all three re-spawned (454–483) | joined; set None in `_join_workers` (641–645) | no | no | via HALT |
| `_state` (347) | READY/ERROR | PLAYING (454) or ERROR re-assert (414) | target (604) | PAUSED (490)/PLAYING (500) | ENDED (908) | ERROR/READY (540/543) |
| `_stop_event`/`_wake_event` (329–331) | — | set→join→clear (420–425) | set (591–592), not cleared | wake set (491) | wake set (585) | via HALT |

Exact diff sets:

- **HALT resets that SP does not** (the bug surface):
  `_head_in_use=False` (596), `_head_trigger.clear()` (597),
  `_head_free.set()` (598), `_head_queue` replacement (599),
  `_release_containers()` (595; both `_live_container` and `_head_container`),
  and unconditional `_seek_event.clear()` (605; SP clears only when set, 435 —
  same end state).
- **SP resets that HALT does not**: `_last_error=None` (430),
  `_applied_seek_generation=_seek_generation` (453), `_pass_start_s`
  (438–446; HALT never touches it — stale after stop, harmless because the
  next SP overwrites it), `_stop_event`/`_wake_event` clear (424–425), worker
  re-spawn, and the `_head_trigger.set()` re-arm (483).
- Note: the presentation thread performs a *third* per-pass reset inline —
  the LOOP branch (913–931: timeline reset, `source_frame_index=None`,
  `processed_index=0`, plus head-protocol set) and `_apply_seek` (949–959:
  queue clear + timeline/indices + head-protocol clear). So "per-pass reset"
  logic exists in four places: SP, HALT, AS, and the LOOP branch. The head
  protocol is deliberately *not* reset by AS (ADR-0024: the head staged for
  the region start is exactly what the seek pass's final boundary needs).

## 2. Stale-head reproduction — CONFIRMED in committed code

Interleaving (looping source, head budget ≠ None so a head thread exists):

1. Live playback reaches a loop boundary: the presentation thread takes the
   LOOP branch (`vs.py:913–931`): under the lock it sets
   `_head_in_use = True` (919), `_head_free.clear()` (922), captures
   `head_queue = self._head_queue` (923); outside, `using_head = True` (925)
   and the head refill trigger is set (930). Verified.
2. Presentation consumes staged head frames: per frame `_present()`
   (892–893) → `_wait_until_due` → waits on `wake_event` (1029). Verified.
3. Main thread: `pause()` (486–491) → state PAUSED, wake set. Presentation is
   parked inside `_present` mid-drain with `_head_in_use=True` and
   `_head_free` still cleared. Verified (inferred parking point: the
   `using_head` branch does not re-check state before the next
   `head_queue.get_nowait()`, 870–872; with an empty remainder it would
   self-heal via 884–889, so the bug window is specifically *mid-drain with
   frames still staged or while pacing a head frame*).
4. Main: `seek(t)` while paused (545–585: records target, sets
   `seek_event`, wakes) — state stays PAUSED.
5. Main: `resume()` → state PAUSED and `seek_event` set (497) →
   `_start_pass(publish_restart=True)` (505).
6. `_start_pass`: `_stop_event.set()` + `_wake_event.set()` (420–421) → the
   parked presentation thread: `_wait_until_playing` returns False
   (1037–1044) → `_present` returns False (990–991) → the `using_head`
   branch exits via `if not self._present(head_item): return` (893) — the
   thread dies **without** clearing `_head_in_use` / setting `_head_free`
   (only the END branch 905–906 and the head-exhausted branch 885–886 do
   that). The LOOP branch's True/clear pair is orphaned. Verified.
   (Alternative death: stop set between the LOOP `continue` (931) and the
   next top-of-loop check (862) — same orphaned flags.)
7. `_start_pass` continues (422–483): joins (old threads dead or exiting),
   clears queue/events, resets timeline/indices/seek filter, spawns fresh
   presentation/decode/head threads, sets `_head_trigger` (483). It never
   touches `_head_in_use`/`_head_free`/`_head_queue` — the new presentation
   thread starts with `using_head=False` (860) and will not set
   `_head_free` again until its own next LOOP/END/seek. Verified.
8. The fresh head worker is triggered (483), restages the region head in its
   kept-open container (815–828), then enters the swap wait (844–856):
   `_head_in_use` still True (stale) → polls `_head_free` (still cleared)
   every 0.1 s until the 2 s deadline (844) → **silent give-up** (852–855:
   plain `break`, no `_warn`, no log). Verified: the give-up path emits no
   warning; `_warnings`/`_last_error` untouched.
9. Consequence at the next loop boundary (or immediately for the trigger of
   step 8): the staged head is dropped; the next LOOP signal makes the new
   presentation thread capture `self._head_queue` (923) — the *old* queue
   (never swapped), empty or stale → immediate fallback to the live pass
   (874–889) → the boundary pays the full pass-restart cost (the exact
   stutter ADR-0024 eliminated). Only observable traces: a presentation
   gap/dropped frame at the boundary; `status()` stays PLAYING; no log line.
   Verified (inferred: mailbox drop behaviour is engine-side, latest-frame
   semantics in `eb.py`).

The ticket's phrasing ("set by the dead presentation thread's LOOP branch …
or set by a LOOP that the restarted presentation never drains") is accurate
for the committed code: the new presentation thread's `using_head` starts
False and nothing on the fresh pass (no LOOP yet) clears the inherited
`_head_in_use`.

Clean paths (no stale inheritance): `stop()`/`close()`/`reload()` all go
through `_halt`, which resets the whole head protocol (596–599); natural END
clears the flags atomically with the ENDED commit (904–908); a seek-while-
*playing* goes through `_apply_seek` which clears them (958–959). So
**resume-from-paused via `_start_pass` is the only dirty entry**, exactly as
the ticket claims.


## 3. `state` write-site inventory

`SourceState` values: `contracts/engine_client.py:50–59`
(CLOSED, READY, PLAYING, PAUSED, STOPPED, ENDED, RECONNECTING, UNAVAILABLE,
ERROR).

**Main/API thread** (all under `self._lock`):
- `__init__` → READY or ERROR (region error), vs.py:347
- `_start_pass` region-error re-assert → ERROR, 413–415
- `_start_pass` pass start → PLAYING, 454
- `pause` PLAYING→PAUSED, 490
- `resume` PAUSED→PLAYING, 500
- `_halt` any-non-CLOSED→STOPPED/CLOSED, 604 (called by `stop` 513,
  `reload` 521, `close` 588)
- `reload` → ERROR (540) or READY (543); `reload` error path → `_fail` 527

**Presentation thread**:
- END branch: if state ∉ {CLOSED, ERROR} → ENDED, 907–908 (commit atomic
  with the head-protocol clear under the lock, 904–908)
- `_fail` when `on_frame` raises (1002–1006 → 1070)

**Decode thread**:
- `_fail` on the decode-failure cap (662–666 → 1070); `_warn` (1064–1067)
  only touches `_warnings`/`_last_error`

So `_fail` (1068–1070 → ERROR) has three owners: decode thread, presentation
thread, and main thread.

Racy transitions and the ordering the code accidentally relies on:

- **ENDED (908) vs a new pass start**: `play()` from ENDED reads state
  (385–386), then `_start_pass` joins the old workers (422) before committing
  PLAYING (454). Today the join's TimeoutError guarantees no *survivor* can
  later run the END branch over the fresh PLAYING. If the join degrades to
  non-raising, a survivor finishing its END branch after 454 overwrites
  PLAYING with ENDED (the guard at 907 excludes only CLOSED/ERROR).
  **This race becomes live only after the join change — the single-writer fix
  must land with it.**
- **`_fail` (1070) vs `reload`/`_halt`**: same structure — a surviving
  worker's late `_fail` could overwrite reload's READY (543) or halt's
  STOPPED, and clobber `_last_error`. Also masked today by the raising join.
- **ENDED vs `stop`/`close`**: serialized by the lock and benign by design —
  `_halt` at 604 commits its target even over ENDED (stopping a just-ended
  source yields STOPPED); 907's exclusion set keeps a late END from
  resurrecting ENDED over CLOSED/ERROR.
- The reset blocks of `_halt`/`_start_pass` (600–609 / 426–453) are safe only
  because all workers are joined dead before them (593 / 422). The raising
  join is the hidden enforcer of "no worker writes after my reset".

## 4. Join contract (current)

`_join_workers` (vs.py:626–645): per alive thread (decode, presentation,
head — tuple order, 630) `thread.join(timeout=5.0)` (636); if any survive,
**raises** `TimeoutError(f"Video worker(s) did not stop within 5 seconds:
{names}")` (639–640); otherwise clears the thread handles (641–645).
Sequential joins: up to 15 s total with three stuck threads. The stuck case
is real, not theoretical: `run_region_pass` polls `interrupted()` only
*between* frames (`region_pass.py:148–150`), and `container.seek` /
`container.decode` (`region_pass.py:95–98, 148`) are C calls — a worker
inside a PyAV call on a broken file cannot observe `stop_event` until the
call returns; a hanging user `on_frame` callback likewise pins the
presentation thread (1002–1004).

Callers that propagate the raise:
- `stop()` (513 → `_halt` 593), `close()` (588 → 593), `reload()`
  (521 → 593), and `_start_pass` (422) — the last reaches `play()` and
  seek-resume `resume()`.
- In-process facade: `InProcessEngineClient.stop` (ipe.py:106–110) is a pure
  delegation to `EngineBody.stop` (eb.py:831–838), which calls
  `source.stop()` **with no suppression** (834–835) → TimeoutError
  propagates to the UI caller, and `facade.panic()` (836–837) +
  `_refresh_transport_state()` (838) are skipped. Same for `reload`
  (840–847). `close` aggregates: `_close_runtime` (eb.py:1226–1246) catches
  per source, `_raise_cleanup_errors` re-raises the first with notes
  (1289–1295) → `EngineBody.close` raises, but state is committed CLOSED in
  `finally` (eb.py:1057–1060).
- Spawned child: `EngineServer._handle_transport` (es.py:517–531) → any
  exception from `engine.stop` is caught by the dispatch loop
  (es.py:396–398) and answered as `CommandFailed` — the child survives. The
  two placements therefore behave differently today for the same source
  failure; the ticket's "stop-path suppression alignment" note resolves
  itself once the source contract stops raising (no change needed in
  ipe.py).
- `_stop_runtime_for_invalid_graph` (eb.py:1201–1212) already suppresses
  per-source stop/close — unaffected.
- `wait_until_finished` (vs.py:576–583) joins only the presentation thread
  with the caller's timeout and returns a bool — already non-raising.

## 5. Design inputs for the fix

### (a) Shared reset function

Split into two lock-held helpers so each existing path keeps its exact
semantics (acceptance: no per-pass state reset in one path but not the other,
for the paths that should share it):

1. `_reset_pass_counters()` — used by **SP, HALT, AS, and the LOOP branch**:
   `_clear_decode_queue()`, `timeline.reset()`, `source_frame_index=None`,
   `processed_index=0`, `seek_filter_pts=None` (the LOOP/AS variants keep
   their current field subsets). Removes the duplicated bodies at
   426–430 / 600–603 / 949–959 / 914–918.
2. `_reset_head_protocol()` — used by **HALT and SP only** (AS and LOOP must
   *not* reset it — ADR-0024 keeps the buffer across seeks and boundaries):
   `_head_in_use=False`, `_head_free.set()`, `_head_trigger.clear()`,
   `_head_queue = queue.Queue(budget)`.

Deliberately **outside** the shared reset:
- `_head_container`/`_live_container` release stays in
  `_release_containers` (611–624), called only by `_halt` — SP must keep
  both containers open across passes (ADR-0024; the head container is reused
  by the restarted head worker at 815). So the `_head_container` release
  belongs in `_release_containers`, not in the shared reset.
- `_state`, `_position_s`, `_pass_start_s`, `_last_error`,
  `_applied_seek_generation`, thread spawn, stop/wake re-arm: path-specific,
  keep inline. Ordering constraint to preserve in both callers: join old
  workers → shared reset → commit state → (SP only) spawn workers → (SP only,
  after spawn) `_head_trigger.set()` (483).

### (b) Single-writer rule for `state`

A pending-transition field committed by the main thread does **not** work:
ENDED must become visible with no subsequent API call (status polling,
`wait_until_finished`, the natural-END reset contract pinned by
`tests/media/test_video_source.py:256–272`), and there is no main-thread
event loop to drain a report. Recommended shape (least code, eliminates the
races found in §3):

- Keep direct commits, but make **worker-side commits conditional on pass
  generation**: add `self._pass_generation` (bumped under the lock in
  `_start_pass`; 0 in `__init__`). Each worker thread captures its
  generation on entry; the END commit (907–908) and `_fail` (1068–1070)
  commit only if `self._pass_generation == <captured>` (checked under the
  lock). A stale survivor (degraded join) can no longer overwrite a fresh
  pass's PLAYING or a reload's READY, and the `_fail` guard also protects
  `_last_error` from stale worker messages.
- Ownership table (document in the class docstring + on the reset helpers):
  **main/API thread** owns READY, PLAYING (pass start/resume), PAUSED,
  STOPPED, CLOSED, region ERROR; **presentation thread** owns ENDED
  (generation-guarded); **any worker** may commit ERROR via `_fail`
  (generation-guarded). Every commit stays under `self._lock`.

### (c) Degraded join contract

- `_join_workers` never raises. On survivors: commit `_state=ERROR` and
  `_last_error = f"Video worker(s) did not stop within 5 seconds: {names}"`
  (committed by the caller thread — the main/API role — under the lock), plus
  a structured log line (add a module logger on the existing
  `synesthesia_machine.engine` logging path per AGENTS.md; the file currently
  has none).
- From `_halt` (stop/close/reload): do **not** commit the nominal
  STOPPED/CLOSED while a worker survived — commit ERROR instead; a source
  whose presentation thread may still be alive is not stopped. `stop()`
  reports this to the engine purely through existing telemetry:
  `SourceStatus.state/last_error` (`vs.py:363–382`) →
  `EngineBody._effective_state` maps any source ERROR to
  `EngineState.ERROR` (eb.py:1263–1267). No wire/protocol change; ADR-0021
  status telemetry carries it.
- From `_start_pass` (play/seek-resume): on survivors, **do not spawn new
  workers over the old ones** (two presentation writers would corrupt the
  mailbox) — commit ERROR and return; the source must be reloaded (matching
  the existing ERROR-requires-reload policy, `play()` 389–397).
- Engine side: nothing to change in ipe.py (pure delegation);
  `eb.py:831–847` becomes exception-free for sources and its
  `facade.panic()`/`_refresh_transport_state()` tail always runs; the child
  path's `CommandFailed` fallback (es.py:396–398) simply stops firing for
  this failure class.

## Recommended implementation plan (ticket shape)

1. Extract `_reset_pass_counters()` + `_reset_head_protocol()` (both
   lock-held); call the counters reset from SP (426–430), HALT (600–603), AS
   (949–959), LOOP branch (914–918); call the head-protocol reset from HALT
   (replacing 596–599) **and SP** (new, inside the 426–453 lock block, before
   thread spawn). Container release stays HALT-only.
2. Add `_pass_generation`; guard the END commit (907–908) and `_fail`
   (1068–1070); add the ownership docstring.
3. Make `_join_workers` non-raising per (c); thread the survivor ERROR commit
   through `_halt`/`_start_pass` as specified.
4. Tests:
   - Regression (acceptance): looping source, park presentation mid head-drain
     (reuse the `_RaceClock`/`_BarrierHeadContainer` machinery from
     `tests/media/test_video_source.py:60–110, 1047–1079`), seek-while-paused,
     resume via `_start_pass`, then assert the head is usable — e.g.
     `source._head_in_use is False` / a fresh swap lands (whitebox, as the
     existing head tests do) or the next boundary stays seamless.
   - Degraded join: hang the presentation thread in `on_frame` (blocking
     callback), `stop()` returns without raising, state is ERROR with the
     worker-name `last_error`, `close()` after release completes cleanly
     (idempotent).
5. No protocol/contract changes (`SourceState`, `SourceStatus` unchanged) —
   spawn compatibility and the engine wire are untouched.

## Tests that pin current behaviour / need attention

None of the existing tests assert the raising join (grep for `TimeoutError`
in `tests/media/test_video_source.py` → no matches), so nothing breaks
directly when the raise is removed.

- **New**: stale-head regression after seek-while-paused resume (acceptance).
- **New**: simulated stuck-worker degradation of `stop()`/`close()`
  (acceptance).
- `tests/media/test_video_source.py:1230`
  `test_head_swap_gives_up_after_bounded_wait` — whiteboxes `_head_in_use`,
  `_head_free`, `_head_trigger`, `_head_queue`, `_lock`; the fix must keep
  those exact field names/semantics (should pass unchanged; the 2 s give-up
  is preserved by design).
- `tests/media/test_video_source.py:419`
  `test_seek_while_paused_applies_on_resume` — the exact scenario of §2;
  must still pass with the head reset added to `_start_pass` (extend it or
  add the sibling regression).
- `tests/media/test_video_source.py:256`
  `test_natural_end_resets_the_source_component`, `:275`
  `test_pause_shifts_anchor_while_stop_resets_processed_index`, `:547`
  `test_stream_without_decodable_frames_still_fails` — pin the ENDED /
  STOPPED / ERROR commits and reset side effects the single-writer rule and
  generation guard must preserve.
- `tests/runtime/test_in_process_cleanup.py:36`
  `test_close_attempts_every_resource_after_failure_and_remains_idempotent`
  — pins engine-level close error aggregation for a *synthetic* failing
  closer; unaffected by the source-side contract change (no edit needed),
  but re-read it if the engine close path is touched.
- `tests/runtime/test_in_process_engine.py:296`
  `test_broken_graph_stops_the_runtime_and_a_valid_graph_restarts_it` —
  exercises the already-suppressing invalid-graph stop path
  (eb.py:1201–1212); unaffected.
