# 02: Reliable observation for run-to-end simulation

**What to build:** The offline MIDI export is the only engine driver that cannot be expressed through the shared protocol: `run_midi_export` constructs `InProcessEngineClient` with escape-hatch constructor kwargs no other caller uses (`tick_observer`, `use_previews=False`, null device catalogue, a custom video-source factory), and `ProcessEngineClient` has no equivalent. Its entire product — the exported note stream — is collected in the `tick_observer` hook, which the engine treats as diagnostic: an observer exception is logged and the tick loop continues, so a broken observer silently truncates the .mid file without a `MidiExportError`. The exporter also re-derives the video source's own frame/region arithmetic (`_SourceFrameCount`, `_processed_frame_count`, `_region_span_s` over the same `inspect_video` metadata the service already consumes) and hides the resulting drift with a `min(1.0, …)` clamp in `MidiExportProgress.fraction` — two owners of "how many frames will this source process", desynchronised in silence.

**Solution:** Make run-to-end simulation a first-class engine mode: observation of the desired state at the MIDI output nodes is a reliable contract (an observation failure surfaces as a stable export error, never a truncated file), and the source publishes its progress facts (processed count, total/region span) through `SourceStatus` so the exporter reads instead of re-deriving. The ADR records the observation contract and the placement decision (why the simulation runs in-process and what the mode interface is).

**Status:** resolved

**Blocked by:** 01

**Files:**
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/media/video_source.py` (status facts)
- `docs/adr/` (new ADR before implementation)
- `tests/runtime/test_midi_export.py`

**Acceptance:**
- [x] An observation failure fails the export with a stable error code; no code path truncates the file silently
- [x] The exporter reads progress from published source status; `_SourceFrameCount`/`_processed_frame_count`/`_region_span_s` are deleted or read published facts
- [x] The export's special construction is expressed through the named mode (or the residual special-casing is named and justified in the ADR)
- [x] ADR written before implementation; protocol/version impact stated
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-19: Implemented as ADR-0025 (protocol v16 -> v17). Decisions: (1) reliability is a consumer-side proof over published facts - after `wait_until_idle` the exporter checks `len(samples[output]) == sum(status.processed_index)` per MIDI output and fails with the stable code `observation_failed` on mismatch; the worker keeps ticking through observer failures (observation is diagnostic to the engine) but logs at most one traceback per worker, so the log pinpoints the fault without per-tick flooding. (2) `SourceStatus` gains `total_index` (computed in `VideoSourceService` from container metadata: frame-count branch preferred, else span/rate) and `region_end_s` (clamped segment end - a whole-file source publishes the file end, not `None`); camera sources publish `None` for both. (3) The exporter's residual special-casing is named and justified in the ADR: the fast-forward playback clock (must own the presentation timeline in the source workers' process), a null device catalogue (a transient engine must not enumerate hardware), and previews disabled (the export consumes no previews). A `ProcessEngineClient` equivalent is deliberately not provided. Tests: `test_status_publishes_presented_frame_total_and_region_end` (media) and `test_export_fails_when_the_observed_stream_is_incomplete` (a client subclass whose observer always raises must surface `observation_failed`); the protocol pin moved to 17 in `test_device_catalogue.py`. Gates: `uv run check` clean; full suite 1379 passed.
- 2026-09-18: From the third architecture-review run (report candidate "One driver, four engine modes"; MIDI-stack scout F2).
