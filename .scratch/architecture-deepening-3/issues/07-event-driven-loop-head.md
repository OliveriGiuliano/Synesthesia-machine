# 07: Event-driven loop-head protocol

**What to build:** The ADR-0024 loop-head worker "refills the buffer after the presentation thread drains it … and idles at zero CPU otherwise" — but `_head_loop` calls `self._head_trigger.wait(timeout=0.05)` and discards the return value, so a timeout falls through into exactly the same full restage as a boundary trigger (`_ensure_head_container` + seek + the entire pre-keyframe discard + conversion of up to `budget` frames, followed by a swap-wait that polls with raw `time.sleep(0.02)`). While a looping source plays the worker therefore re-seeks and re-decodes the head continuously — on ADR-0024's own reference file each restage costs the measured ~152 ms prefix discard plus frame conversion, so the third thread spends close to 100% of a core — and the idling property the ADR uses to justify the extra PyAV container and decoder state does not exist in the implementation. The follow-up `self._head_trigger.clear()` can also wipe a trigger set mid-staging (a lost wakeup) that only the periodic restage masks.

**Solution:** Distinguish the two outcomes of the wait: a timeout continues without restaging (the worker truly idles at zero CPU); only a boundary trigger (pass start, or the presentation thread draining the staged head) sets the refill trigger; the clear/wake protocol is made race-free so a trigger set mid-staging is honoured by the next iteration. ADR-0024's stated property becomes true as written.

**Status:** ready-for-agent

**Blocked by:** 06

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `tests/media/test_video_source.py`
- `docs/adr/0024-predecoded-loop-head.md` (amend the description to note the implementation gap that was closed)

**Acceptance:**
- [ ] While a looping source plays and no boundary is pending, the head worker performs no decode work (observable through the fake-container seam)
- [ ] A trigger set mid-staging is not lost
- [ ] The boundary gap stays under one frame interval on the existing real-fixture tests
- [ ] The ADR-0024 idling property holds as written (ADR description amended if needed)
- [ ] Media tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (video-source scout F1 — implementation contradicts ADR-0024's stated consequence). Ordered after 06 because the shared pass module restructures the worker body.
