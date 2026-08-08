# Phase 6 node reference

Phase 6 adds seven production definitions: four visual-to-musical algorithms and three MIDI-state
utilities. They operate on immutable runtime values, return desired `MidiStateFrame` state, and never
open a MIDI port or emit raw MIDI messages. Send MIDI remains the sole hardware boundary.

## Shared musical controls

Scanline, Edges to Pitch, Fourier, and Optical Flow use the same control group:

| Control | Contract |
| --- | --- |
| Root note / Scale | Select a built-in scale from a pitch-class root. `custom` uses the persisted 12-bit custom scale mask. |
| Minimum / Maximum MIDI note | Inclusive 0–127 range. Only scale notes inside both endpoints are eligible. |
| MIDI channel | UI and persistence use 1–16; runtime note keys use 0–15. |
| Maximum polyphony | Strongest candidates win; stable note order resolves equal strengths. |
| Minimum / Maximum velocity | Inclusive 1–127 output range. Strength is deterministically mapped and clamped here. |

Candidate notes are scale-constrained, duplicate pitches merge by maximum strength, and polyphony is
applied after duplicate resolution. These semantics are owned by the shared Qt-free musical helper
and exposed through one reusable editor group/view model.

## Synesthesia algorithms

### Scanline

- **Type:** `synmachine.synesthesia.scanline`
- **Ports:** Channel `value` → MIDI state `midi`
- **Controls:** bottom-to-top, top-to-bottom, or ping-pong direction; rows advanced per processed tick;
  line thickness; mean/maximum band aggregation; activation threshold; velocity curve exponent.
- **State:** position advances only after a successfully processed input. It does not advance through
  `NoData`, skipped ticks, or source drops. Reset, input-height change, and state-significant parameter
  changes restart the scan deterministically.
- **Algorithm:** extracts a horizontal band, normalizes through the channel nominal range, sanitizes
  non-finite samples, area-resizes it to the allowed-note count, then maps surviving samples to
  velocity strengths.

### Edges to Pitch

- **Type:** `synmachine.synesthesia.edges_to_pitch`
- **Ports:** edge-mask Channel `edges` → MIDI state `midi`
- **Controls:** external/tree contour retrieval; minimum area/perimeter; contour work limit; independent
  pitch and velocity features; explicit feature ranges.
- **Features:** perimeter, area, normalized centroid X/Y, orientation, circularity, plus edge strength
  for velocity.
- **Algorithm:** converts the normalized finite mask to binary, extracts and spatially orders OpenCV
  contours, caps work before musical selection, skips degenerate contours, and maps chosen features
  through explicit clamped ranges.

### Fourier

- **Type:** `synmachine.synesthesia.fourier`
- **Ports:** Channel `value` → MIDI state `midi`
- **Controls:** none/Hann/Hamming window; optional mean subtraction; radial/horizontal/vertical frequency
  mapping; explicit frequency and log-amplitude ranges; DC exclusion; mean/percentile band aggregation;
  activation threshold.
- **Algorithm:** normalizes and sanitizes the channel, applies the selected window, computes a 2D FFT
  and `log1p` magnitude, then maps fixed spatial-frequency bands to allowed notes.
- **Cache:** one read-only shape-dependent band map is reused while shape, allowed-note count, mapping,
  frequency range, and DC exclusion remain unchanged. Reset and close clear the cache.

### Optical Flow

- **Type:** `synmachine.synesthesia.optical_flow`
- **Ports:** equal-size, same-clock Images `current` and `reference` → MIDI state `midi`
- **Controls:** stable Fast/Balanced/Accurate Farnebäck presets; minimum motion; direction, position, or
  magnitude pitch; mean/maximum magnitude or moving-pixel-fraction velocity; grid size; cell-note or
  global-histogram aggregation; fixed magnitude normalization range.
- **Algorithm:** converts both inputs to finite uint8 luminance locally, calculates dense reference-to-
  current Farnebäck flow into a typed float32 buffer, aggregates it deterministically, and maps motion
  through the common musical contract.

## MIDI-state utilities

### Multiply Velocity

- **Type:** `synmachine.utility.multiply_velocity`
- **Ports:** MIDI state `midi` → MIDI state `midi`
- Multiplies active velocities by a finite non-negative factor, rounds deterministically, clamps to
  1–127, and removes every note when the factor is zero.

### Transpose

- **Type:** `synmachine.utility.transpose`
- **Ports:** MIDI state `midi` → MIDI state `midi`
- Shifts notes by −127 through +127 semitones, preserves channel/velocity, drops results outside 0–127,
  and merges any resulting duplicates by maximum velocity.

### MIDI Merge

- **Type:** `synmachine.utility.midi_merge`
- **Ports:** variadic MIDI states `midi_1`, `midi_2`, … → MIDI state `midi`
- Requires at least two inputs but has no upper input-count limit. All inputs must match the execution
  clock; duplicate channel/note keys resolve to maximum velocity in stable numeric port order.

## Runtime and diagnostic contract

Required-input `NoData` is propagated by the Scheduler before these runtimes execute. Runtime validation
failures become recoverable node errors and `NoData` outputs. Stateful reset ownership remains with the
Scheduler's source-component lifecycle. All nodes execute in the Qt-free engine and automatically
appear in normal per-node profiler timing; no algorithm code lives in the UI.

The persisted catalogue and four sample graphs satisfy the documentation/catalogue part of the node
definition of done. Deterministic algorithm, validation, non-finite, `NoData`, reset, frame-skip, and
source-drop cases are covered in `tests/phase6`. Repeatable 500×500 microbenchmark evidence is stored in
`docs/phase-6-benchmarks.json`.
