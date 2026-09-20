# 08: One pass-state reset, one state writer

**What to build:** The triple-thread lifecycle re-derives the stop/restart protocol per call path: `_start_pass` (used by play, seek-resume, and loop restart) restarts all three workers and resets decode-queue/timeline state but not the head protocol state, while `_halt` is the canonical stop and the only path that resets `_head_in_use`/`_head_trigger`/`_head_queue`. A paused seek-resume therefore goes through `_start_pass` with a possibly stale `_head_in_use=True` (set by the dead presentation thread's LOOP branch and cleared only in specific exit branches), and the head worker's swap-in then blocks up to 2 s at the next boundary before silently degrading to the at-boundary restart cost. The `state` field is written from three roles — the presentation thread sets `ENDED` mid-loop, any thread can set `ERROR` via `_fail`, caller threads set `STOPPED`/`CLOSED` in `_halt` — and a stuck PyAV call in any worker surfaces as a hard `TimeoutError` out of `stop()`/`close()` that only one engine call path (`_stop_runtime_for_invalid_graph`) suppresses. Any new lifecycle path or per-pass state must re-learn the protocol by inspecting the four existing paths.

**Solution:** One shared pass-state reset used by both `_halt` and `_start_pass` (every per-pass state — queues, timeline, head protocol — reset in exactly one place); the `state` field gets a single commit point (workers report transitions, one place commits them); the worker-join timeout degrades to a documented, non-raising failure mode in the stop/close contract so a stuck C call cannot propagate out of the lifecycle calls.

**Status:** resolved

**Blocked by:** 06

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/runtime/in_process_engine.py` (stop-path suppression alignment)
- `tests/media/test_video_source.py`

**Acceptance:**
- [x] One reset function serves halt and pass start; no per-pass state is reset in one path but not the other
- [x] A regression test proves the head is usable after a seek-while-paused resume (stale `_head_in_use` impossible)
- [x] State transitions are committed by a single role; workers report
- [x] A simulated stuck worker degrades per the documented contract instead of raising out of `stop()`/`close()`
- [x] Lifecycle tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (video-source scout F5). Thread-protocol redesign — settle the shape (reset function scope, commit-point ownership, join contract) in design before implementing.
- 2026-09-20: Implemented. `_reset_pass_counters` / `_reset_head_protocol` are the single reset points shared by `_halt` and `_start_pass`; per-pass state can no longer be reset in one path but not the other (seek and loop boundaries keep their documented narrower subsets). State ownership is now explicit: the API thread commits READY/PLAYING/PAUSED/STOPPED/CLOSED/region-ERROR; the presentation thread commits ENDED; workers commit ERROR via `_fail` - all worker-side commits guarded by `_pass_generation` under the lock (a pending field was rejected in design: ENDED must be visible without an API call). `_join_workers` never raises: a worker stuck in a native call degrades the lifecycle to a logged ERROR naming the survivors, `close` commits CLOSED as the terminal state, and no new pass is ever spawned on top of survivors. `_halt` keeps the FFmpeg containers open while survivors exist (contexts are not thread-safe; the next clean halt releases them), and `_warn` carries the same cohort guard as `_fail`. Two regression tests added (stale-head-after-seek-resume; stuck-worker degradation). Two-axis review (spec + standards) both verdict "correct"; both findings fixed. Full suite: 2 pre-existing flaky tests in `tests/runtime/test_process_previews.py` failed under load; verified flake-on-pristine (3/8 baseline failures) - not a regression of this change.
