# ADR 0017: Optional reduced-resolution analysis for the synesthesia analysis nodes

- Status: Accepted
- Date: 2026-09-07

## Context

Phase-2 stress testing with a dense synesthesia graph at native 1080p showed the
analysis algorithms themselves, not per-frame overhead, are the bottleneck:
dense Farnebäck optical flow takes ~295 ms and a 1080p Fourier transform ~97 ms
per frame on a high-end desktop, so a complex graph processes at a few frames
per second and would be far slower on the supported 4-core laptop. Both
algorithms scale with pixel count (Farnebäck roughly linearly, the FFT about
quadratically), and most musical mappings (radial/linear frequency bands,
motion direction, per-region aggregates) are resolution-relative: they describe
*proportions* of the frame, so analysing a reduced copy of the frame yields
the same musical structure with far less work. Users with slow machines or
with real-time requirements therefore need a way to trade spatial detail for
speed, without changing the default behaviour of existing graphs.

## Decision

The **Optical Flow** and **Fourier** nodes each gain one additive,
connectable integer parameter, `analysis_max_dimension`, default **0**:

- `0` (default) means full-resolution analysis; existing graphs and the
  historical behaviour are preserved exactly, including the validation that
  grid dimensions cannot exceed image dimensions.
- Any positive value caps the longest side of the analysis frame at that many
  pixels. The frame (optical flow: both input images; Fourier: the input
  channel) is reduced proportionally with bilinear interpolation only when it
  is larger than the cap; smaller frames are analysed as-is. For Optical Flow
  the colour frame is reduced first and the (more expensive, per-pixel)
  luminance conversion runs on the reduced copy, so the whole analysis
  pipeline executes at analysis resolution.
- For Optical Flow the Farnebäck displacement is expressed in the reduced
  frame's pixels and is rescaled back to the original frame's pixel scale
  (displacement × original/reduced scale per axis) before magnitude
  aggregation, so `magnitude_minimum`/`magnitude_maximum` and the motion
  threshold keep their full-resolution meaning.
- For Fourier the band maps are built for the reduced frame dimensions; the
  maps are already resolution-relative (normalized frequencies), so the
  band-to-note arrangement is unchanged. Absolute log-magnitude levels are
  not scale invariant, so users who keep fixed `amplitude_floor`/
  `amplitude_ceiling` values while switching resolutions may want to re-tune
  them; the parameter's help text says so.
- Reduced analysis changes what detail the music can follow (sub-pixel and
  fine texture), which is the intended trade; the parameter is exposed in the
  inspector and can be driven by a connected scalar value.

The change is additive: `parameter_values` already fills missing parameter
keys with the definition default, so documents saved before this parameter
existed load and run unchanged with the default `0`. No implementation
version bump or migration is required.

## Consequences

- Complex 1080p graphs can reach real time on modest hardware by analysing at
  e.g. 512 px (reducing the pixel count by ~11×), at the cost of fine motion
  and high-frequency detail in the musical mapping.
- Defaults are unchanged, so existing graphs and all existing tests keep their
  behaviour; new tests pin (a) bit-identical results at `0` and (b) reduced
  results equal to running the historical full-resolution pipeline on a
  pre-reduced copy of the frame (plus the flow rescale for Optical Flow).
- Optical Flow's grid dimension validation now applies to the analysis frame
  when reduced; grids valid at full resolution can be rejected by very small
  caps, reported with the existing error text.
- Downstream consumers of the nodes' outputs see the same output types and
  semantics; only the analysed detail level changes when the parameter is
  raised from 0.

## References

- Phase-2 stress evidence: dense 1080p graph at 2.0 processed fps (optical
  flow p95 295 ms, Fourier p95 97 ms) versus the 60 fps budget.
- Phase-1 workstreams left these algorithmic costs in place on purpose:
  behaviour-affecting changes were deferred to Phase 3.
