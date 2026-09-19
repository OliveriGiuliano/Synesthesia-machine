# ADR 0025: Run-to-end simulation as a first-class engine mode

- Status: Accepted
- Date: 2026-09-18
- Context: ADR-0016 (offline MIDI export over looping videos), ADR-0019 (the
  export's on/off-only event policy), ADR-0021 (video source seek and loop
  segments), protocol v16

## Context

The offline MIDI export is the only engine driver that cannot be expressed
through the shared `EngineClient` protocol. It builds an `InProcessEngineClient`
with escape-hatch constructor arguments no other caller uses (a `tick_observer`
hook, `use_previews=False`, a null device catalogue, a fast-forward
video-source factory), and `ProcessEngineClient` has no equivalent. Two of its
fragile edges are real hazards:

1. **Observation is diagnostic, but the observation is the product.** The
   entire export — the note stream — is collected in the `tick_observer` hook,
   which the graph worker treats as a diagnostic: an observer exception is
   logged (once per tick, once per source) and the tick loop continues. A
   broken observer therefore silently truncates the `.mid` file instead of
   failing the export, and a per-tick failure floods the log with one
   traceback per frame.
2. **The exporter re-derives the source's own arithmetic.** The exporter keeps
   its own copy of the frame/region math the video source already performs
   (`_SourceFrameCount` probes the file again with `inspect_video`;
   `_processed_frame_count` and `_region_span_s` recompute the region span and
   presented-frame total from container metadata), so the two can only agree
   by duplication.

## Decision

- **The observation contract.** In run-to-end simulation the engine observes
  the desired state at the MIDI output nodes once per processed source frame.
  Reliability is a consumer-side proof over published facts: the exporter
  verifies that the number of observed states per MIDI output node equals the
  total frames the sources report having processed when the run ends, and any
  mismatch fails the export with the stable `MidiExportError` code
  `observation_failed`. No code path can truncate the file silently. The graph
  worker keeps treating an observer exception as diagnostic — it must never
  stop the tick loop that feeds the real outputs — but it records at most one
  traceback per run (the first failure) instead of one per tick, so the log
  pinpoints the fault without flooding.
- **Published progress facts (protocol v16 → v17).** `SourceStatus` gains two
  fields the video source already computes but never published: `total_index`
  (the processed index the source will have reached at the end of its active
  region; `None` when the container reports neither a frame count nor a frame
  rate) and `region_end_s` (the clamped end of the played segment). Camera
  sources publish `None` for both. The exporter reads progress and the
  simulated timeline end from these fields; `_SourceFrameCount`,
  `_processed_frame_count`, and `_region_span_s` are deleted from the
  exporter. The menu eligibility probe keeps a single `inspect_video` call per
  video source as its readability gate.
- **Placement and residual special-casing.** Run-to-end simulation stays
  in-process, and the export keeps three named construction choices: the
  fast-forward playback clock (virtual time jumps to each frame's presentation
  deadline, so the whole video simulates at decode speed while every frame
  keeps its real PTS — this clock must own the presentation timeline in the
  same process as the source workers), a null device catalogue (a transient
  engine must not enumerate hardware), and previews disabled (the export
  consumes no previews). A `ProcessEngineClient` equivalent is deliberately
  not provided: the child process cannot own the fast-forward clock that drives
  its own sources' presentation loop, and the IPC boundary adds nothing to a
  batch simulation.

## Consequences

- Protocol v17: the single shared `ENGINE_PROTOCOL_VERSION` constant is bumped
  on both sides of the seam; a version-mismatched pair already refuses the
  handshake, so no compatibility shim is needed. Existing `SourceStatus`
  consumers (UI status normalisation, process-client tests) ignore the two
  additive fields.
- The export's progress total is known from the first status poll after play
  instead of from a pre-probe of the file; the `None` total is reported until
  the sources publish.
- A source whose container reports no frame count and no frame rate publishes
  `total_index=None` (indeterminate progress) exactly as the exporter computed
  before, but the exporter's wrong `0` for a duration-less whole-file pass is
  corrected by the source-side computation.
- The exporter fails at the end of the run, not at the first failed tick: the
  simulation runs to completion and the mismatch is detected before the file is
  written. The first failure is logged at the tick that failed.

## References

- ADR-0016 (in-process export placement), ADR-0019 (export event policy),
  ADR-0021 (seek and loop segments), ADR-0024 (pre-decoded loop head)
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/contracts/engine_messages.py`
