# ADR 0023: Video loop editors take their bounds from engine-published duration

- Status: Accepted
- Date: 2026-09-17

## Context

The Load Video node's loop start/end editors bound their timestamp sliders to
the video's real duration. The bound was produced by a definition metadata hook
(`parameter_editor_resolver`), which opened the file's container with PyAV on
the UI thread and cached the probe result at module level. That contradicted
ADR-0005's process boundary (file I/O belongs in the engine process, where the
source already opens the container) and AGENTS.md's rule that the UI event
loop must not be blocked by device or file I/O. The compromise — a probe plus
cache inside the definition layer — had never been recorded as a decision.

The engine already publishes what the editors need: the video source reports
`SourceStatus.duration_s` (alongside `file_path`) on the existing status
channel, so the duration crosses the process boundary as data.

## Decision

1. `ParameterEditorResolver` is a pure value function
   `(spec, values, source_status) -> spec`. A definition metadata hook performs
   no I/O and opens no device or file; the only runtime knowledge it receives
   is the `SourceStatus` value the engine published for that node.
2. The UI keeps the latest source statuses (keyed by node id) and passes them
   to `project_graph`; the Load Video resolver bounds its loop sliders with
   `status.duration_s` when the status's `file_path` matches the node's
   configured file, and leaves the editors unbounded otherwise. Re-projection
   is gated on the editor-relevant fields (file, duration) so per-tick cursor
   movement does not re-project the view model.
3. The UI-thread header probe and its module-level duration cache are removed
   from the definition layer. `inspect_video` stays in `media` as the engine
   process's probe.

## Consequences

- The UI thread performs no file I/O for editor metadata; the definition
  stays a testable value object whose resolver is a plain function.
- Loop slider bounds appear once the engine reports the source status (i.e.
  once the source has started with a readable file). Until then — for a
  never-started or unreadable source — the editors stay unbounded and their
  interactive scale is inert (slider/track and fields disabled): the UI
  presents no substitute range, so a user cannot select a time that lies
  outside the video's real range. The UI thread never probes file headers.
- Resolvers that need no runtime knowledge (e.g. the crop mode presentation
  rule) receive the status argument and ignore it; the protocol stays uniform.

## References

- ADR-0005 (UI/engine process separation) — this decision closes the
  unrecorded compromise that placed a bounded header probe in the UI process.
- ADR-0021 (video source seek and loop segments) — the loop editors this
  bounds policy feeds.
