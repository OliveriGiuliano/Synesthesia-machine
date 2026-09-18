# 05: An empty pass is an end, not an error

**What to build:** Seeking to the end of the played segment — the normal action of the UI's own controls (`VideoPlaybackControls._nudge` clamps to `duration_s`; the slider's far right maps to full duration) — lands the video source in a fatal state. The service's `seek()` clamps the target to `region_end_s`; a pass starting at `start_s >= end_s` discards every frame (`pts < start_s`) and, on a typical file where the last frame precedes `duration` by one frame interval, presents none, so `decoded_any` stays `False` and `_decode_once` raises `"video stream contains no decodable frames"`. Three consecutive failures flip the source to `ERROR` and `play()` raises `RuntimeError("Video source must be reloaded after an error")` until `reload()`. The source cannot distinguish "the user asked for the end" from "corrupt stream" — both surface as `decoded_any is False` — and the fatal path is untested: `test_seek_clamps_to_the_played_segment` asserts `status().source_time_s` only and never plays.

**Solution:** Treat a pass whose range contains no presentable frame as a clean end of the pass (park at the requested position; the loop's existing restart rules apply on the next play), while genuinely empty or corrupt streams (the demuxer reports no packets at all) keep the recoverable error. Add the regression tests: seek to `region_end` while playing, then play — the source must not enter `ERROR`.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/ui/playback_controls.py` (verify the clamp contract only)
- `tests/media/test_video_source.py`

**Acceptance:**
- [x] A seek landing at or beyond the region end while playing leaves the source in a playable state (no `ERROR`, no forced `reload()`)
- [x] A stream with no decodable packets still surfaces a recoverable error
- [x] Regression tests: seek to `region_end` → play → not `ERROR`; corrupt/empty stream → error preserved
- [x] Existing media tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (report candidate "The video source pass, once"; video-source scout F4 — user-reachable fatal path). Safe early step in the same module as ticket 06; land this first so 06 rewrites the pass logic with the edge already fixed.
- 2026-09-18: Implemented. `_decode_once` now reports whether the pass range contained any presentable frame, and `_decode_loop` ends the source (END, not LOOP) when it did not - for looping sources too, so seeking to the region end stops playback instead of auto-restarting from the segment start. Two cases make a range hold no frame: (a) `start_s >= end_s` (a seek clamped to the region end) - detected before touching the container, because seeking at/past the last frame makes the demuxer yield nothing, so "did the demuxer yield frames" cannot tell this case from an empty stream; (b) the demuxer yielded frames but none fell in the range - a clean end, distinct from (c) a stream that yields no frames at all, which still raises and trips the repeated-failure error (test: fake container via monkeypatched `av`). Two traps the regression exposed in `_start_pass`, now closed: a fresh pass clears the seek PTS filter (`_seek_filter_pts`), which a seek-to-end pass left armed at the region end and would otherwise drop every frame of the next pass; and a pending seek whose target is at or beyond the region end is dropped in favour of the segment start, or every `play()` after such a seek would park at the end again. `playback_controls.py` verified only: `_nudge` and the slider's far right already clamp to `duration_s`, so the UI always seeks to exactly the region end - the path this ticket fixes.
