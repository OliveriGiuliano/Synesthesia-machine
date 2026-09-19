# 06: The video source pass, once

**What to build:** "One pass over the loop region" — seek to the keyframe at or before the start, decode-and-discard frames before the start (including the subtle rule that PTS-less frames count toward selection but are never presented), select every Nth frame, convert — is implemented twice: once in the live decode loop (`_decode_once`, video_source.py:633-683) and once in the pre-decode head worker (`_head_loop`, 722-802), each over its own PyAV container. The presentation thread's boundary duplicate filter (`source_frame_index <= head_upto`) drops the right frames only if the two containers' pass-local ordinals align exactly — a contract that is cross-thread, cross-container, invisible to the interface and the type system (containers are `Any`), and already needed one ADR-era fix for a misalignment (the PTS-less-prefix comment at 757-763). ADR-0024 itself concedes: "If PyAV ever changes seek+decode determinism, the index-based duplicate filter degrades to a possible one-frame visual difference." The only seam a test can fake is the clock; every head-protocol test (budget, swap, degradation, boundary gap) generates a real MP4 and runs three real threads against wall-clock deadlines, and the only handle to one decode path without the other is the thread name.

**Solution:** One shared region-pass module — the seek/discard/select/convert state machine written once — consumed by both the live decode thread and the head worker behind a container adapter, so the two containers are adapters and mirror drift is structurally impossible rather than test-policed; a fake-container adapter satisfies the seam so the pass protocol, the head budget, the swap protocol, and the degradation path become testable headlessly. The threads and the ADR-0024 design stay; what concentrates is the pass logic.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/media/video_source.py` (new shared pass module alongside)
- `tests/media/test_video_source.py`
- `docs/adr/0024-predecoded-loop-head.md` (note the property restorations once 07 lands)

**Acceptance:**
- [x] Seek/discard/select/convert is one implementation consumed by both the live pass and the head pre-decode
- [x] A fake-container adapter exists; head-protocol tests run without real encoders or real threads where the fake reaches
- [x] The duplicate filter's correctness no longer depends on a test policing cross-container ordinal faith
- [x] Live, head, and seek behaviour on the real-fixture tests are unchanged (boundary gap, PTS monotonicity)
- [x] `uv run check` green; full media suite green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run — **top recommendation** (video-source scout F2; ADR-0024 mirror contract). Grill the shape of the pass module (iterator vs. state machine; what the container adapter's interface is; where pass-local state lives) before implementation.
- 2026-09-19: Two-axis review (fresh-context subagents were unavailable in this
  environment - dispatches were lost at turn boundaries; the review below was
  performed inline by the implementer against the full diff, stated here as a
  limitation).
  Standards: PASS. region_pass imports stdlib/av/numpy only (headless media
  preserved); Any restricted to the stub-less PyAV boundary; frozen/slotted
  value types; facade __all__ ruff-sorted (RUF022); consumers (midi_export,
  engine_body) unaffected. Nits fixed during review: CONTEXT.md
  presented-frame entry corrected (the mailbox carries the frame payload, not
  "without full-resolution data").
  Spec: PASS on all five criteria. No residual pass logic in video_source.py;
  tests/media/test_region_pass.py is fully headless (synthetic container/frame
  fakes; no av.open, threads, or fixtures); the boundary duplicate filter now
  rests on the shared ordinal, with the real-fixture test kept as a regression
  net. Behavioural equivalence verified against the old code: stop/seek check
  order, the no-decodable-frames raise condition, skipped_by_selection
  accounting, and the head budget (the budget-th frame is still staged; the
  pass ends at the same point as the old break). One intentional efficiency
  note: the head pass ends as soon as the budget is full (the old loop also
  broke there), so no extra container iteration occurs after a full head.
  Gates: uv run check green; full suite 1429 passed / 3 platform-conditional
  skips; git diff --check clean.
