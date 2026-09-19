# CONTEXT.md

Domain glossary for Synesthesia Machine. Entries name concepts and point at
their owner; decisions and their reasoning live in `docs/adr/`, the
behavioural baseline in `docs/architecture/master.md`.

## Source concepts

- **Region pass** - one playback pass over a region of a video file: seek to
  the keyframe at or before the pass start, decode and discard the frames
  before the start, select every Nth in-range frame, and convert the
  selected frames. A single shared state machine
  (`synesthesia_machine.media.region_pass`) that both the live decode thread
  and the loop-head pre-decoder execute over their own container adapters, so
  the two cannot drift from each other (ADR-0024).
- **Loop head** - the bounded buffer of pre-decoded region-start frames that
  lets a loop boundary present without a stall. The head worker runs the
  region pass in its own container; the presentation thread serves the head
  only immediately after a loop signal and drops the duplicate frames the
  restarted live pass decodes (ADR-0024).
- **Presented frame** - the packet a source publishes after PTS pacing: the
  source frame index, presentation timestamp, and the frame payload, carried
  through the engine's bounded latest-frame mailbox.
- **Source clock** - the PTS-derived monotonic timeline a source paces
  presentation on; all values derived from a frame carry its source clock
  identity (`FrameContext`).
