# ADR 0024: Pre-decoded loop head for seamless loop restarts

- Status: Accepted
- Date: 2026-09-18

## Context

ADR-0021 confines a looping Load Video source to the segment
`[loop_start_s, loop_end_s)`, but every pass restart by re-opening the PyAV container
from disk, seeking to the keyframe at or before the region start, and decoding through
(discarding) every frame before the region start. That cost lands exactly when the
bounded presentation queue runs dry, so the gap at each loop boundary equals it.
Measured on a 1280x720 h264 file with a 10 s keyframe interval and an 8 s mid-GOP loop
region (start 46.3 s, end 54.3 s): container reopen ~6 ms, demuxer seek ~0.03 ms,
pre-keyframe discard ~152 ms (81 frames), and the on-frame gap across the boundary
171-179 ms against a 40 ms frame interval - a visible stutter on every loop cycle, and
proportionally worse for files with larger keyframe intervals.

## Decision

- The live decode container stays open across passes of one source instance; it is
  re-opened only when the file changes (reload) or the source is closed. Every pass
  re-seeks on the open container to the keyframe at or before the pass start.
- A source-owned pre-decode worker (a short-lived third thread alongside the existing
  decode and presentation workers) keeps a bounded buffer of the *loop head* warm: in
  its own container it reproduces the region start's pre-keyframe discard and decodes
  the first `N` frames of the region into a bounded head queue. `N` is the smaller of
  one second of source video at the published rate and the region's presented-frame
  count, floored at 2; sources without a usable frame rate do not pre-decode. The
  worker refills the buffer after the presentation thread drains it at a boundary (or
  when a new pass starts; a seek leaves the buffer in place - see below) and idles at
  zero CPU otherwise.
- Head frames are only valid at natural loop boundaries, where the pass restarts at
  the region start. The presentation thread consumes the head buffer only immediately
  after a loop signal, paces those frames on the same PTS timeline as live frames, and
  then drops the duplicate frames the restarted live pass decodes (matched by
  `source_frame_index`, which the head worker reproduces exactly: same seek, same
  pre-start discard, same every-Nth frame selection, and the same treatment of frames
  without a usable PTS - counted toward selection but never staged or presented). A
  seek does not clear the buffer: the interrupted pass restarts at the seek target,
  so the head staged for the region start is exactly what the loop boundary that ends
  that pass needs.
- The head buffer is capped at the documented budget of pre-decoded frames (uint8 RGB
  before the float32 conversion that happens at presentation time, as for live frames;
  about 2.8 MB per 720p frame, so roughly 69 MB for a second of video at 25 fps). It
  never grows with loop length or resolution beyond the frame-rate-scaled cap. A
  region that cannot be pre-decoded in time (for example a keyframe interval larger
  than the budget) degrades to today's at-boundary restart cost - never worse, without
  failure and without unbounded retention.
- Non-looping sources keep today's behaviour (no worker); they reuse the kept-open
  container across replays.

## Consequences

- The loop boundary costs only what the presentation thread still owes the tail of the
  previous pass. On the measured file the boundary gap drops from ~175 ms to under one
  frame interval.
- A looping source holds one extra PyAV container (decoder state) while playing, plus
  up to the head budget of decoded frames; both are released on stop, reload, and
  close, and the worker thread joins on the same lifecycle paths as the other workers.
- `source_frame_index`, `processed_index`, the loop signal, and the source-clock
  semantics downstream are unchanged, so the scheduler and engine protocol are
  untouched.
- If PyAV ever changes seek+decode determinism, the index-based duplicate filter
  degrades to a possible one-frame visual difference at the boundary, not to a stall.

## Update

- 2026-09-19: The "reproduces exactly" mirror is now one implementation. The
  seek/discard/select/convert state machine moved to the shared region pass
  (`synesthesia_machine.media.region_pass`), which the live decode thread and
  the head worker both execute over their own container adapters;
  `DecodedVideoFrame` and the frame conversion moved with it. The decisions
  above are unchanged; only the mechanism that keeps the head from drifting
  from the live pass is different (one shared machine instead of two
  hand-kept copies).
- 2026-09-20: The idling property is now enforced by the wait protocol,
  not assumed (ticket 07). The worker's trigger wait distinguishes a
  timeout (re-wait at zero CPU, no restage) from a latched trigger (exactly
  one restage; a trigger set while the worker is mid-staging latches and is
  honoured by the next iteration), and its swap wait is event-driven on a
  head-free event the presentation thread sets when a head consumption
  finishes (drain, seek, end, halt) instead of sleep-polling, keeping the
  bounded give-up deadline. The presentation thread also sets the refill
  trigger when a staged head drains before the pass ends.
