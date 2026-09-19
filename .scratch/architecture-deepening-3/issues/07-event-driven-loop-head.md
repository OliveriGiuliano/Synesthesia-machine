# 07: Event-driven loop-head protocol

**What to build:** The ADR-0024 loop-head worker "refills the buffer after the presentation thread drains it … and idles at zero CPU otherwise" — but `_head_loop` calls `self._head_trigger.wait(timeout=0.05)` and discards the return value, so a timeout falls through into exactly the same full restage as a boundary trigger (`_ensure_head_container` + seek + the entire pre-keyframe discard + conversion of up to `budget` frames, followed by a swap-wait that polls with raw `time.sleep(0.02)`). While a looping source plays the worker therefore re-seeks and re-decodes the head continuously — on ADR-0024's own reference file each restage costs the measured ~152 ms prefix discard plus frame conversion, so the third thread spends close to 100% of a core — and the idling property the ADR uses to justify the extra PyAV container and decoder state does not exist in the implementation. The follow-up `self._head_trigger.clear()` can also wipe a trigger set mid-staging (a lost wakeup) that only the periodic restage masks.

**Solution:** Distinguish the two outcomes of the wait: a timeout continues without restaging (the worker truly idles at zero CPU); only a boundary trigger (pass start, or the presentation thread draining the staged head) sets the refill trigger; the clear/wake protocol is made race-free so a trigger set mid-staging is honoured by the next iteration. ADR-0024's stated property becomes true as written.

**Status:** resolved

**Blocked by:** 06

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `tests/media/test_video_source.py`
- `docs/adr/0024-predecoded-loop-head.md` (amend the description to note the implementation gap that was closed)

**Acceptance:**
- [x] While a looping source plays and no boundary is pending, the head worker performs no decode work (observable through the fake-container seam)
- [x] A trigger set mid-staging is not lost
- [x] The boundary gap stays under one frame interval on the existing real-fixture tests
- [x] The ADR-0024 idling property holds as written (ADR description amended if needed)
- [x] Media tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (video-source scout F1 — implementation contradicts ADR-0024's stated consequence). Ordered after 06 because the shared pass module restructures the worker body.
- 2026-09-19: Research (scout) confirmed the ticket with line-level detail:
  `_head_loop` waits on `_head_trigger` with a 0.05 s timeout and discards the
  return value, so an idle timeout falls through into the same full restage as
  a real trigger (restage ~152 ms on the ADR file, far longer than the 50 ms
  tick - the worker is perpetually mid-staging and never idles); the
  drain-complete site (the presentation thread's `_head_in_use` False-flip)
  sets no trigger at all, so refill after consumption is only caused by the
  next 50 ms tick; the swap-wait polls `_head_in_use` with raw
  `time.sleep(0.02)` that the injectable-clock test fakes cannot
  fast-forward, and on the 2 s deadline the staged buffer is dropped.
  Plan: (1) branch on the `wait()` return value - a timeout means re-wait at
  zero CPU, only a set trigger causes exactly one restage; route the wait
  through the injectable clock so tests can fast-forward idle periods;
  (2) set the trigger at drain-complete (and at pass start, as today) so
  refill is caused by consumption, not by polling; a trigger set mid-staging
  latches in the Event and is honoured by the next iteration (not lost);
  (3) replace the sleep-poll with a wait on a head-free event (set under the
  existing lock when `_head_in_use` flips False) driven by the injectable
  clock, keeping the bounded 2 s give-up; (4) tests: a counting
  fake-container seam asserting flat head decode/seek calls across a
  fast-forwarded quiescent window, a mid-staging trigger-set regression, a
  clock-driven swap-timeout case, with the existing boundary-gap tests
  unchanged. ADR-0024 gets an amendment note that the idling property is now
  enforced by the wait protocol, not assumed.
- 2026-09-20: Implemented and resolved. The worker's trigger wait now goes
  through the injectable clock and branches on the result: a timeout
  re-waits at zero CPU, a latched trigger causes exactly one restage; the
  presentation thread sets the refill trigger when a staged head drains
  before the pass ends; the swap wait is event-driven on a new `_head_free`
  event (cleared under the lock when head consumption starts, set under
  the lock at drain/seek/END/halt) with the bounded 2 s give-up kept.
  Implementation review (fresh-context, two-axis) found and fixed two
  self-inflicted regressions before merge: a `timeout=` keyword against the
  clock protocol's `timeout_s` parameter (killed the head thread under
  every clock, including the production one) and a dropped
  `if item is _QueueSignal.LOOP:` guard that orphaned the LOOP branch
  behind the END branch's `return` (crashed the presentation thread at
  the first boundary). Both shapes are pinned by the new tests. New tests:
  `test_head_worker_idles_between_boundaries` (counting fake-container
  seam: flat head seek/decode calls across a paused fake-time window,
  restage after resume crosses the next boundary),
  `test_head_trigger_set_mid_staging_is_not_lost` (barrier on the head
  container's first decode; presentation paused so the latched trigger is
  the only one observable), `test_head_swap_gives_up_after_bounded_wait`
  (in-use head dropped, not force-swapped, past the clock-driven bounded
  wait; retry swaps in). ADR-0024 amended with an Update bullet. Verified:
  `tests/media/` 86 passed; `tests/runtime/` 154 passed; remainder of the
  suite 1278 passed (the full suite was verified in those two parts after
  one full-run pass wedged on a preview test for ~30 min on a loaded
  dev machine while the same test passed in 0.86 s standalone and the
  whole runtime directory passed in isolation); `uv run check` green.
