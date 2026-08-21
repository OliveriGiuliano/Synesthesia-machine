# Example graphs

Current executable graphs and their contained deterministic media live in `examples/library`; the
generated all-definition catalogue lives in `examples/catalogue`.

## Graphs

- `hue-chord.synmachine.json` — video → resize/colour channels → desired notes → image and note
  visualization. Debug audio is persisted disabled and must be enabled deliberately.
- `reference-image-pipeline.synmachine.json` — video → resize → Gaussian Blur → HSV, then branches to
  Channel to Pitch/Note Visualizer and Canny/Channel Display.
- `motion-grid.synmachine.json` — current and one-frame-held images feed Optical Flow and Note
  Visualizer.
- `edge-ensemble.synmachine.json` — Canny → Edges to Pitch → Multiply Velocity → Note Visualizer.
- `scanning-score.synmachine.json` — HSV saturation → Scanline → Note Visualizer.
- `spatial-spectrum.synmachine.json` — luminance → Fourier → Transpose → Generate Audio. Audio is
  persisted disabled.

The graphs use Load Video and safe sinks so opening one never selects a camera or MIDI output. Add a
Send MIDI node only after choosing the exact output port you intend to use.

`catalogue/current.synmachine.json` contains every current production definition exactly once in
stable registry order. It is intentionally disconnected and exists to verify registration,
persistence, and migrations.

Canonical loading, relative-media resolution, compilation, topology, output safety, and schema
round trips are covered by `tests/integration/test_catalogue_and_examples.py` and
`tests/integration/test_image_examples.py`. Algorithm controls are documented in
`docs/reference/node-reference.md`.
