# 19: Keep video metadata hooks off the UI thread

**What to build:** A definition metadata hook (_load_video_parameter_editor) opens a PyAV container on the UI thread: view-model projection calls it, it probes file headers, and a module-level duration cache is the mitigation. That puts device/file I/O on the Qt event loop, in tension with ADR-0005's process boundary and AGENTS.md's 'do not block the UI event loop with device I/O' — and a definition metadata resolver is supposed to be a value operation.

**Solution:** Make the resolver pure (spec, values) -> spec with the video duration supplied as data that crosses the process boundary through the existing engine status channels; if the header probe must stay in the UI process for responsiveness, record a new ADR that explicitly blesses a bounded header-only probe and its cache.

**ADR:** Requires a new ADR either way: either blessing the bounded header probe or recording the redesign — the current state is an unrecorded compromise.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/input/video.py`
- `src/synesthesia_machine/ui/view_models.py`
- `src/synesthesia_machine/media/video_source.py`
- `docs/adr/ (new ADR if the probe is blessed)`

**Acceptance:**

# Answer

Shipped (duration arrives as data; ADR-0023 records the redesign, resolving the unrecorded compromise with ADR-0005): `ParameterEditorResolver` is now the pure `(spec, values, source_status) -> spec` protocol. The UI keeps the latest `SourceStatus` values (main window forwards engine telemetry to the session, which gates re-projection on the editor-relevant fields file+duration so per-tick cursor movement is a no-op) and passes the matching status into `project_graph`. The Load Video resolver bounds its loop sliders with the engine-published `status.duration_s` when the status file matches the configured file; the PyAV header probe, its module-level cache, and the mtime key are gone from the definition layer, and `inspect_video` stays in `media` as the engine process's probe. Crop's mode-presentation resolver conforms to the uniform signature. New tests: tests/nodes/input/test_video.py (bounds from published duration, loop-end clamp, file mismatch/missing duration/missing file stay unbounded, resolver never probes the filesystem) and updated crop-resolver call sites.
- [ ] Definition stays a testable value object
- [ ] UI thread performs no file I/O
- [ ] Cache + probe vanish if duration arrives as data
- [ ] Tension with ADR-0005 resolved on the record
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
