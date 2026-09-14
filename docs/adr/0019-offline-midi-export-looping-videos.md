# ADR 0019: Offline MIDI export simulates looping videos as a single pass and observes every MIDI output node

- Status: Accepted
- Date: 2026-09-14

## Context

ADR-0016 rejected graphs whose video sources are configured to loop: the menu
action was disabled (and the export API raised a stable `looping_source`
error) because a looping source never reaches end-of-video, so there was no
finite "start to end of the video" to export.

In practice this behaviour was confusing and blocked a common workflow: users
loop a video so the instrument keeps playing, and the same saved graph is the
only one they have. The disabled Export MIDI button gave no useful path to
their notes; the natural reading of "export the video" is one pass from the
start of the file to its end, which is exactly what the fast-forward clock
already simulates.

ADR-0016 also scoped the exported notes to the desired state reaching Send-MIDI
nodes. A graph whose MIDI leaves the document through Generate Audio (the debug
synthesizer) instead produced nothing to export even though the user hears the
notes in the live engine.

## Decision

This ADR supersedes two consequences of ADR-0016; the rest of ADR-0016 stands.

- Exporting a looping video simulates a single pass from the start of the video
  to its end, as if the loop parameter were not set. The export source factory
  constructs every video source with looping disabled, so the fast-forward
  clock reaches the end of the video and the sources report ENDED, ending the
  simulation deterministically. The saved graph is not modified.
- The exporter records the desired MIDI state reaching every MIDI output node:
  Send MIDI and Generate Audio. The offline engine still substitutes null MIDI
  output and null debug-synth services, so no port, note, or audio is produced
  during the export. Graphs with neither node type are still rejected with
  `no_midi_output`.

The stable `looping_source` failure code and the menu's loop-based disabling
are removed; the Export MIDI action is enabled for any graph with finite video
sources, present media, at least one MIDI output node, and a valid graph.

## Consequences

- A looping source no longer blocks export; the exported file contains the
  notes of exactly one pass over the video, matching what the live graph plays
  on its first pass.
- Notes that drive the debug synthesizer are exported alongside Send-MIDI
  notes; when both node types are present and receive different states, the
  exported event stream is the union of both.
- The `looping_source` code is removed from the export API and the UI; no
  migration or protocol change is needed because the code was only ever
  produced, never persisted.

## References

- `docs/adr/0016-offline-midi-export.md`
- `docs/adr/0004-midi-stack.md`
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/ui/midi_export.py`
