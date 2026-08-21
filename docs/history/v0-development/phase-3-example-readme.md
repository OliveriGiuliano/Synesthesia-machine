# Phase 3 Hue Chord example

Open [`hue_chord.synmachine.json`](hue_chord.synmachine.json) in Synesthesia Machine and press
**Play**. The saved graph loops a generated hue sequence through the complete Phase 3 path:

```text
Load Video → Resize (500×500) → HSV → Separate Hue → Channel to Pitch
                                             ├→ Note Visualizer
Resize ──────────────────────────────────────┤→ Display Image Data
                                             └→ Generate Audio
```

`Generate Audio` is deliberately disabled in the saved file so opening the example never accesses
an audio device. To hear the diagnostic synth, select that node, enable **Enable audio output**, and
play the source. Use **MIDI → Panic** (or `Ctrl+Shift+Esc`) to silence it immediately.

The video path is stored relative to this graph. The included media is deterministic and generated
by `tools/generate_test_video.py`; automated tests regenerate the same seven-bin sequence rather
than depending on an audio device or hand-authored media. To recreate it manually:

```powershell
uv run python -c "from pathlib import Path; from tools.generate_test_video import generate_hue_test_video; generate_hue_test_video(Path('examples/phase3/media/hue_chord_demo.mp4'), width=64, height=64)"
```