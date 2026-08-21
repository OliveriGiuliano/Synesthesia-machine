# Phase 6 completion report — Synesthesia algorithms and MIDI utilities

## Outcome

Phase 6 is complete. The production registry contains 58 definitions, the frozen Phase 5 51-node
catalogue remains an unchanged historical subset, and the four new synthesis algorithms plus three
MIDI-state utilities meet the architecture's node definition of done. All processing remains Qt-free,
hardware-independent, deterministic on synthetic fixtures, and stops before Phase 7.

## Ordered implementation and checkpoints

The required implementation order was preserved in small non-amended checkpoints:

| Order | Work | Commit |
| ---: | --- | --- |
| 1 | Shared musical parameters, editor group/view model, and mapping helper | `ab19264843b5983b6c72700550eddf4273ac8568` |
| 2 | Multiply Velocity, Transpose, unbounded variadic MIDI Merge | `5cad27cc807e420f52993212ac9a896cd9c9d68f` |
| 3 | Stateful Scanline | `dc57e523e45832948d96d6023ee956c97ae9b31c` |
| 4 | Edges to Pitch | `9ad56b35e8b570b55d8fc9d0cff34b4fb99c088e` |
| 5 | Fourier and shape cache | `3573eb0fdbe06f06328b889f4358b2c06596207d` |
| 6 | Optical Flow and Farnebäck presets | `7428d5c89c6f85a987c0a8380916aa8fbe07926c` |
| 7 | Catalogue, reference graphs, diagnostics, evidence, and documentation | This final `feat: complete phase 6 integration` checkpoint, parented by `7428d5c8` |

The Phase 5 final checkpoint `b22338ee6af16ce298ffc006acb05e3a555ab1ca` remains in the direct
lineage. No earlier checkpoint was amended.

## Implemented algorithms and choices

### Shared musical foundation

All synthesis nodes resolve root, built-in/custom scale, inclusive MIDI range, persisted channel 1–16,
maximum polyphony, and inclusive velocity range through one helper. Candidate duplicates merge by
maximum strength before strongest-note selection. Equal strengths use stable note order. The reusable
editor group is metadata-driven; no musical algorithm was introduced into the UI.

### MIDI utilities

- Multiply Velocity accepts a connectable finite non-negative factor, clamps results to 1–127, and
  treats factor zero as an empty desired state.
- Transpose preserves channel and velocity, drops notes outside 0–127, and deterministically resolves
  collisions.
- MIDI Merge accepts the unbounded numeric `midi_1`, `midi_2`, … port family, requires at least two
  same-clock states, and merges duplicate keys by maximum velocity.

None of these utilities opens a port or emits raw MIDI. Send MIDI remains the only MIDI hardware sink.

### Scanline

Scanline normalizes a horizontal channel band, sanitizes non-finite samples, area-resizes it to the
allowed-note count, and applies threshold and velocity-curve controls. Position is runtime state:
successful processed ticks advance it; `NoData`, skipped ticks, and dropped sources do not. Reset,
height change, direction change, and significant parameter replacement restart deterministically.

### Edges to Pitch

Edges to Pitch uses OpenCV contour extraction over an explicitly normalized binary edge mask. Contours
are sorted spatially, capped before feature processing, filtered by area/perimeter, and rejected when
degenerate. Perimeter, area, normalized centroid, orientation, circularity, and edge-strength features
map through persisted explicit ranges rather than frame-relative implicit normalization.

### Fourier

Fourier applies optional mean subtraction and a none/Hann/Hamming window, computes a 2D FFT with
log-magnitude scaling, and aggregates radial, horizontal, or vertical frequency bands by mean or
percentile. Frequency/amplitude ranges, DC exclusion, and activation are explicit. A read-only band map
is cached by the complete shape-dependent key and cleared on reset/close; output does not depend on
cache history.

### Optical Flow

Optical Flow validates equal shape and same input/execution clock, converts descriptor-aware images to
finite uint8 luminance, and calculates dense reference-to-current Farnebäck flow. Fast, Balanced, and
Accurate preset IDs own stable OpenCV parameters. Grid-cell or global-histogram aggregation maps fixed
direction, position, or magnitude pitch features and fixed motion-strength velocity features through
explicit magnitude ranges.

## Interfaces and safety

- Image/Channel inputs remain immutable read-only C-contiguous float32 arrays; outputs are immutable
  desired `MidiStateFrame` values.
- Algorithms never mutate inputs, open devices, synchronize clocks, resize mismatched inputs, or emit
  raw MIDI messages.
- Required-input `NoData` propagates at the Scheduler boundary. Recoverable malformed inputs and
  parameters become structured node errors and `NoData` outputs.
- Same-clock validation is explicit for every MIDI utility and synthesis path. Optical Flow also
  requires equal dimensions.
- Reset/close behavior is implemented for every runtime. Scanline state and Fourier cached maps are
  released at their documented lifecycle boundaries.
- Every definition has registry metadata, parameter validation, deterministic unit coverage, this
  short reference, catalogue inclusion, sample-graph coverage, profiler visibility, and no UI
  algorithm code.

Detailed port and parameter behavior is in [`phase-6-node-reference.md`](phase-6-node-reference.md).

## Persisted catalogue and examples

[`examples/phase6`](../examples/phase6) contains:

1. Motion Grid: deterministic Load Video → Resize 500×500 → current plus Hold Image(1) reference →
   Optical Flow → Note Visualizer.
2. Edge Ensemble: Load Video → Canny → Edges to Pitch → Multiply Velocity → Note Visualizer.
3. Scanning Score: Load Video → HSV → Saturation channel → Scanline → Note Visualizer.
4. Spatial Spectrum: Load Video → Luminance → Fourier → Transpose → disabled Generate Audio.
5. A disconnected catalogue containing all 58 current registry definitions exactly once.

The graphs adapt the architecture's camera/MIDI sketches to deterministic included media and safe
sinks. They production-load, compile, and round-trip canonically. None contains Send MIDI; Generate
Audio is persisted disabled. The unchanged `examples/phase5/catalogue.synmachine.json` still contains
exactly the historical 51 definitions and is tested as a strict subset of the Phase 6 catalogue.

## Tests and final acceptance

Final acceptance was run on Windows 11 x64 with CPython 3.12.13:

| Command | Result |
| --- | --- |
| `uv run pytest -q tests/phase6` | Passed; 107 Phase 6 tests across all seven ordered batches. |
| `uv run pytest -q` | Passed; 706 tests including all Phase 0–5 and smoke regressions. |
| `uv run check` | Passed; 176 files formatted, Ruff clean, strict Pyright 0 errors/warnings/information. |
| `$env:QT_QPA_PLATFORM='offscreen'; uv run synmachine --smoke-test` | Passed; production application composition started and stopped normally. |
| `uv run python -m tools.phase6_benchmarks` | Passed; four stable 500×500 output states and finite timing samples. |
| Phase 5 persistence subset gates | Passed; frozen 51-node catalogue is unchanged, canonical, and a strict subset. |
| `git diff --check` | Passed before the final checkpoint. |

The Phase 6 suite covers common scale/range/channel/polyphony/velocity invariants; unbounded variadic
ports; deterministic translations, contours, scan sequences, and sinusoids; parameter validation;
non-finite input; `NoData`; reset; frame-skip and source-drop stability; cache invalidation/reuse;
catalogue/schema round trips; production graph compilation; hardware-safe sinks; and benchmark schema
and state repeatability.

## 500×500 diagnostic evidence

The canonical report is committed as [`phase-6-benchmarks.json`](phase-6-benchmarks.json). Each fixture
is built before timing, every algorithm receives an immutable 500×500 input, three calls warm the path,
and ten calls are measured with `time.perf_counter_ns`. Every warm-up and measured call must return the
same non-empty MIDI state. Fixture SHA-256 values make accidental fixture drift visible.

| Algorithm | Median | p95 | Stable output notes | Observation |
| --- | ---: | ---: | ---: | --- |
| Scanline | 0.055 ms | 0.063 ms | 8 | Middle five-row mean band |
| Edges to Pitch | 1.548 ms | 1.632 ms | 3 | Two rectangles, circle, ellipse |
| Fourier | 10.771 ms | 11.394 ms | 8 | Cache: 1 miss, 12 hits |
| Optical Flow | 48.923 ms | 50.126 ms | 2 | Seeded texture translated +4 px X |

The report records HEAD `7428d5c89c6f85a987c0a8380916aa8fbe07926c` and
`git_working_tree_dirty=true` honestly because evidence was generated while assembling this final
checkpoint. The environment includes NumPy 2.5.1 and OpenCV 4.13.0.92 on a 24-core/32-thread AMD64
machine.

This evidence is deliberately a per-algorithm microbenchmark. It does not claim the master
architecture section 18.6 full-graph release methodology: no five-second/30-second graph run, source
or processed FPS, drops, p50/p95/p99 end-to-end latency, UI/process/decoder/device cost, or median of
three release runs is measured. Timing equality is not a gate; deterministic states and finite
non-negative samples are.

## Limitations

- Dense Farnebäck flow is CPU-bound and is the slowest measured algorithm on the recorded machine.
- Contour and FFT cost depends on image content, chosen work limits, note count, and aggregation; the
  deterministic fixtures are comparison anchors rather than worst-case bounds.
- Scanline is intentionally stateful and scans horizontal rows only.
- Fourier caches one current shape/configuration map rather than an unbounded multi-shape cache.
- Examples use included video instead of physical cameras and safe visual/disabled-audio sinks instead
  of live MIDI. Hardware behavior remains covered by the established opt-in Phase 4 evidence path.
- The committed timing values are one local microbenchmark run and must not be used as a universal
  throughput guarantee.

## Architecture deviations

No architecture deviation, schema migration, dependency addition, or ADR was required. The safe
persisted graph adaptations and direct-algorithm benchmark scope are evidence choices documented under
their limitations, not changes to runtime or hardware-boundary architecture.

## Next work

Phase 6 is complete. Phase 7 is the next eligible phase and was intentionally not started by this
checkpoint.
