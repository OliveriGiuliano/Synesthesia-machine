# Phase 3 — First end-to-end video-to-note vertical slice

## Objective

Deliver the first genuinely usable instrument graph and validate the runtime semantics before implementing the full catalogue.

## Nodes in scope only

Load Video; Resize; Change Colour Space; Separate Channels; Channel to Pitch; Note Visualizer; Generate Audio; Display Image Data. Add Image to Luminance only if required by clean fixed typing.

## Essential semantics

- PyAV drives file decode and presentation timestamps.
- Real-time pacing follows frame PTS, including variable frame rate.
- `process_every_nth_frame=N`: N=1 processes all; N=2 processes source frames 1,3,5… without slowing playback.
- emitted processed index starts at 1 and increments only for frames sent into the graph.
- images are RGB normalized float32 on entry.
- channel views are read-only.
- Channel to Pitch uses valid-pixel masks, histogram occupancy relative to valid pixel count, threshold-to-velocity linear remap, scales/range/polyphony, and outputs desired MIDI state.
- Generate Audio reads desired state through a safe snapshot and uses a non-blocking sounddevice callback.
- previews are throttled and never execute Qt in engine code.

## Allowed paths

`media/video_source.py`, colour-space/conversion modules, named node folders, `midi/scales.py`, `midi/debug_synth.py`, preview facade, transport UI, and relevant tests/docs.

## Ordered tasks

1. Implement colour-space descriptors and conversion utilities.
2. Implement Load Video source lifecycle: open/play/pause/stop/reload, PTS pacing, loop/reset, bounded decode queue.
3. Implement image runtime conversion and Resize.
4. Implement Change Colour Space and Separate Channels.
5. Implement scale registry and musical selector.
6. Implement Channel to Pitch exactly as specified.
7. Implement compact note-state publication and Note Visualizer.
8. Implement callback-safe debug synth.
9. Implement image preview publication and Display Image UI.
10. Wire source transport and status through EngineFacade.

## Required tests

- generated CFR and VFR video pacing;
- Nth-frame index sequence;
- stop versus pause reset behaviour;
- colour conversion and channel metadata;
- fan-out of one decoded frame without mutation;
- Channel to Pitch known histogram/threshold cases, empty valid mask, scale/range/polyphony;
- debug synth voice lifecycle with mock callback buffer;
- preview throttling;
- end-to-end generated video → expected MIDI states.

## Performance check

Run 500×500 video through this graph for 60 seconds. Report processed FPS, p95 node time, drops, memory, and UI responsiveness. This phase is diagnostic; final optimization is later.

## Exit criteria

A saved graph plays a real video, shows it, generates visible notes, and produces simple audio without stuck voices. The UI remains responsive.

## Completion report

Implemented; graph example path; interfaces; tests/results; measured performance; limitations; deviations; next work.
