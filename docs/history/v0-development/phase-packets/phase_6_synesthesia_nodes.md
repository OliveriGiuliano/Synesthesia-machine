# Phase 6 — Synesthesia algorithms and MIDI utilities

## Objective

Implement the distinctive visual-to-musical algorithms on top of the proven runtime and MIDI-state contracts.

## Scope

Optical Flow, Edges to Pitch, Scanline, Fourier, Multiply Velocity, Transpose, MIDI Merge, and shared musical controls.

## Common contract

Each synesthesia node resolves root/scale/inclusive MIDI range, channel, velocity range, and maximum polyphony. It generates candidate note strengths, merges duplicate notes by maximum strength, selects strongest notes, and returns MidiStateFrame. It never opens a port or emits raw messages.

## Optical Flow

Two same-clock/equal-shape IMAGE inputs: current and reference. Initial algorithm is dense Farnebäck on luminance. Use explicit fixed motion normalization by default. Provide presets and grid aggregation. Synthetic translations must map predictably.

## Edges to Pitch

CHANNEL edge mask input. Extract contours; calculate perimeter, area, centroid, orientation, circularity; map selected features through explicit ranges. Skip degenerate contours and cap work before polyphony selection.

## Scanline

CHANNEL input. Stateful row position advances on processed ticks, not skipped/dropped source frames. Resize line to allowed-note count; position maps pitch and sample maps velocity. Reset on lifecycle and height changes.

## Fourier

CHANNEL input. Optional mean subtraction/window; FFT/log magnitude; cached radial band map; DC exclusion; explicit frequency/amplitude ranges. Avoid per-frame reconstruction of shape-dependent maps.

## MIDI utilities

- Multiply Velocity clamps 1–127; factor zero removes notes.
- Transpose drops out-of-range notes.
- MIDI Merge is variadic, same-clock, duplicate=max velocity.

## Ordered tasks

1. shared musical parameter widget/view model and algorithm helper;
2. MIDI utilities and tests;
3. Scanline;
4. Edges to Pitch;
5. Fourier with shape cache;
6. Optical Flow with presets;
7. example graphs and diagnostics.

## Required tests

Known synthetic translations, contour shapes, scanline sequences, sinusoidal spatial frequencies, scale/range/polyphony invariants, reset behaviour, duplicate merge, and dropped-frame stability. Include benchmark results per algorithm at 500×500.

## Exit criteria

All algorithms produce deterministic expected note states on synthetic fixtures, respect common musical rules, and remain safe under NoData, resets, frame skipping, and source drops.

## Completion report

Implemented; algorithm choices; interfaces; tests/results; benchmarks; limitations; deviations; next work.
