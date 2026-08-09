# Phase 6 reference graphs

This directory contains the current 59-node catalogue plus four persisted examples for the Phase 6
synesthesia algorithms. Every executable graph uses the deterministic video already committed at
`../phase5/media/phase5_reference.mp4`, loops it through the production Load Video source, and can be
opened directly in Synesthesia Machine.

## Graphs

- `motion_grid.synmachine.json` — Load Video → Resize 500×500, with the resized current image and a
  one-frame Hold Image reference feeding Optical Flow → Note Visualizer.
- `edge_ensemble.synmachine.json` — Load Video → Canny → Edges to Pitch → Multiply Velocity → Note
  Visualizer.
- `scanning_score.synmachine.json` — Load Video → HSV → Separate Channels (Saturation) → Scanline →
  Note Visualizer.
- `spatial_spectrum.synmachine.json` — Load Video → Luminance → Fourier → Transpose → Generate Audio.
  Generate Audio is persisted with `enabled=false`; enable it deliberately after selecting an
  appropriate audio device and volume.
- `catalogue.synmachine.json` — every current production registry definition exactly once, in lexical
  type-ID order. The disconnected catalogue is a persistence/instantiation smoke artifact rather
  than an executable graph.

The architecture diagrams recommend camera and MIDI hardware for some examples. These persisted
acceptance versions use deterministic Load Video and safe sinks so opening a graph never selects a
camera, opens a MIDI port, or starts audio. Add Send MIDI explicitly only after choosing the exact
output port you intend to use.

The four graphs production-load, round-trip canonically, and compile without warnings or errors.
Their exact topology and safe-output settings are acceptance-tested in
`tests/phase6/test_batch7_examples_diagnostics.py`. Algorithm behavior, controls, and safety rules
are summarized in `docs/phase-6-node-reference.md`.
