# Phase 5 — Utility and image-processing node library

## Objective

Implement the broad visual-processing vocabulary using shared semantics and conformance tests, not one-off node code.

## Scope

Image Dimension, Adjustments, Filters, Utilities, Channels, Hold Image, and missing scalar bridge nodes: Channel Statistics, Remap Number, Float to Integer, Image to Luminance, Channel Display.

## Fixed image contract

- float32 arrays; RGB-family values nominally 0–1;
- explicit colour-space descriptors;
- immutable inputs;
- alpha behaviour documented per node;
- no implicit colour conversion or resize in multi-input nodes;
- NoData and non-finite policies;
- common border/interpolation enums;
- no Python pixel loops.

## Implementation batches

1. Resize refinements, Crop, Flip, Rotate.
2. Brightness, Contrast, Clamp, Colour Levels, Hue, Saturation, Invert, Opacity, Stretch Contrast, Gamma.
3. Gaussian Blur, Sharpen, Add Noise, Posterize.
4. Threshold, Canny, Convolve, Dilate, Erode, High Pass, Low Pass.
5. Blend Images, Combine/Separate Channels, Image to Luminance.
6. Channel Statistics and scalar bridge utilities.
7. Hold Image and memory diagnostics.

Each batch should be a separate reviewable coding-agent request if practical.

## Required shared utilities

- colour-space registry and channel selection;
- validated odd-kernel helpers;
- finite-value sanitation/reporting;
- border/interpolation conversion;
- alpha split/recombine helpers;
- array read-only guard used in tests;
- node conformance fixture that checks metadata, clock, shape, dtype, NoData, and input mutation.

## Hold Image rules

`delay_frames=1` outputs the immediately previous processed frame. Until history is full, output NoData. Reset on stop/seek/loop, source-clock change, shape/colour-space change, or delay change. Display estimated memory.

## Required tests

Every node gets algorithm cases plus shared conformance. Include alpha and non-finite cases where applicable. Add a catalogue smoke graph that instantiates every definition. Add a 30-minute accelerated loop/soak test for bounded memory.

## Exit criteria

Every node meets the master document's definition of done; input-mutation tests pass; documentation and registry metadata are complete; the catalogue can be loaded without unknown definitions.

## Completion report

Implemented by batch; interfaces; tests/results; performance/memory observations; limitations; deviations; next work.
