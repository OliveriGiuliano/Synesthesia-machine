# ADR 0016: Offline MIDI export through a transient in-process engine

- Status: Accepted
- Date: 2026-09-15

## Context

Users want to take the notes their graph produces from a video file and load them
into a DAW (for example Ableton Live) as a Standard MIDI File. The live engine
cannot produce that artifact: it emits desired-note state to a physical MIDI port
in real time, it prefers recent frames over complete coverage, and its previews
are throttled latest-value payloads. A complete, deterministically timed export
needs the graph to run over every video frame from the start to the end of the
video, at full speed, without touching any hardware.

## Decision

File > Export MIDI runs a one-shot **offline simulation** in the UI process:

- The editor spawns a **transient `InProcessEngineClient`** (the same protocol
  implementation the test and smoke suites already exercise) that lives only for
  the duration of the export. It is independent of the running live engine,
  which keeps producing or staying idle as before; ADR-0005 process separation
  for the live path is unchanged.
- Video sources are driven by a **fast-forward playback clock** injected through
  the existing `PlaybackClock` seam: virtual time jumps to each frame's
  presentation deadline, so the decode/presentation loop runs at full speed while
  every frame keeps its real PTS timestamp. Frames are still decoded and the
  scheduler still executes the real graph, so the exported notes are exactly the
  notes the live graph would have produced.
- The export registry substitutes a **null MIDI output service** for the physical
  `MidiOutputService` and a null debug synth, so no MIDI port is opened, no note
  is sent, and no audio is played, even if the graph contains enabled
  Send-MIDI/Generate-Audio nodes.
- A per-tick observer hook on the in-process graph worker hands each tick's
  result and source frame to the exporter. The exporter records the desired
  `MidiStateFrame` reaching every Send-MIDI node together with the source frame's
  video timestamp, then derives note-on/note-off events from consecutive state
  diffs. The video ends the simulation; all still-sounding notes are stopped at
  the end time.
- The resulting events are written as a **Standard MIDI File** (format 0, single
  track, 960 ticks per quarter). Because video time is not a tempo grid, each
  event interval is followed by a tempo meta event that makes one tick equal that
  interval, so DAWs place the notes at their true video times.

The engine wire protocol, graph schema, and live engine lifecycle do not change.

## Consequences

- Export works only for graphs whose sources are all Load Video nodes (camera
  sources have no finite end, and a looping video has no "end of the video")
  and requires at least one Send-MIDI node; the menu action is disabled
  otherwise and explains why. A looping source is rejected with a stable error
  code rather than run for one arbitrary pass, because silently ignoring the
  graph's loop parameter would export something the document does not describe.
- With several video sources the exported timeline is a union of the sources'
  own PTS time bases: each tick is stamped with the PTS of the frame that
  triggered it. Sources with non-zero start offsets or unsynchronized clocks
  therefore contribute their own offsets; the common case (zero-based,
  same-length videos) is exact.
- The UI shows a small progress bar over a dimmed, locked window while the
  export runs (progress = processed frames / total video frames) and can be
  cancelled; the transient engine is closed on completion, failure, and cancel.
- The transient engine decodes full-resolution video in the UI process, so very
  long or very high-resolution videos can use substantial memory and CPU; this
  is accepted for a one-shot background job that does not run in real time.
- The in-process client is an implementation of the shared `EngineClient`
  protocol; the UI only calls the headless `runtime` export API and never
  depends on engine-process internals.

## References

- `docs/adr/0005-ui-engine-process-separation.md`
- `docs/adr/0004-midi-stack.md`
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/midi/smf.py`
