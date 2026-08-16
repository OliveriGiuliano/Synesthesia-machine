# Phase 5 catalogue and reference graphs

This directory contains two persisted acceptance artifacts with different purposes.

## Executable reference graph

Open [`reference_graph.synmachine.json`](reference_graph.synmachine.json) and press **Play**. It
adapts the master architecture's reference graph to the existing deterministic Load Video source:

```text
Load Video → Resize 500×500 → Gaussian Blur 5×5 → HSV → Separate Channels
                                                        └→ Hue → Channel to Pitch → Note Visualizer
                                      HSV image → Canny → Channel Display
```

The included relative-path video is a small deterministic generated fixture. The graph has no camera,
audio, or MIDI-output sink, so loading and playing it does not open output hardware. Recreate the
fixture if needed with:

```powershell
uv run python -c "from pathlib import Path; from tools.generate_test_video import generate_hue_test_video; generate_hue_test_video(Path('examples/phase5/media/phase5_reference.mp4'), width=64, height=64)"
```

## Catalogue load smoke

[`catalogue.synmachine.json`](catalogue.synmachine.json) contains every built-in registry definition
exactly once. It is intentionally disconnected: its purpose is to prove that the persisted catalogue
loads and instantiates without unknown type IDs or unsupported versions, and to provide a browsable
node reference. It is **not** an executable graph because nodes with required inputs are unconnected.

The catalogue includes source and output definitions for completeness, but loading it does not start
sources or access hardware. Automated tests compare its supported 50-node sequence with the production
registry and round-trip both files through the schema-versioned persistence boundary.
