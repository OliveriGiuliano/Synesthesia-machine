# Phase 5 completion report

## Status

Phase 5 — Utility and image-processing node library is complete on Windows 11 x64 using the
project-local, uv-managed CPython 3.12 environment.

The phase delivers the complete ordered node matrix across dimensions, adjustments, filters,
analysis, compositing/channels, scalar bridges/display, and bounded temporal state. The production
registry contains 51 unique built-in definitions. Its image catalogue contains 34 definitions in
stable display order, including the import-compatible Phase 3 Change Colour Space utility. Every
Phase 5 matrix row has algorithm coverage and shared conformance coverage, with alpha and non-finite
cases where applicable.

Final integration adds explicit utility/catalogue module ownership, persisted executable and
all-definition graphs, and a canonical 108,000-tick bounded-memory report. All Phase 1–4 behavior is
retained. No Phase 6 implementation is included.

## Implementation checkpoints

The immutable Phase 5 batch checkpoints are:

- `db9129ab32e47d54627b30aa775b941c3d5511ea` — `feat: add phase 5 image dimensions batch`
- `38b0229e3a318bff7ed0607f7e79a56b1817d039` — `feat: add phase 5 image adjustments batch`
- `7ef2ce85e3ec67e1955fbf21a4e7a435a115b292` — `feat: add phase 5 image filters batch`
- `9a9aed8bc83376891589bab82e496cca7c3ad1f0` — `feat: add phase 5 analysis filters batch`
- `2450879349c5e5809d68f9606fdb9e3d4e1bbf13` — `feat: add phase 5 compositing channels batch`
- `a37417b1f260635404fbee3e624afbcc11eb4b9c` — `feat: add phase 5 scalar bridges batch`
- `d2a6cf7a0a1f07006d39eb08e84c8d232afc4e76` — `feat: add phase 5 hold image batch`

The separate non-amended checkpoint containing this report owns final catalogue integration,
persisted examples, soak tooling/evidence, and completion documentation. Its parent is the exact
Batch 7 commit `d2a6cf7a0a1f07006d39eb08e84c8d232afc4e76`.

## Implemented by batch

### Batch 1 — Image dimensions

- Resize supports explicit dimensions, fit modes, aspect preservation, deterministic padding, and
  shared interpolation policy.
- Crop supports normalized or pixel coordinates, clamping or padding, and descriptor-aware border
  colour.
- Flip supports horizontal, vertical, and combined transforms.
- Rotate supports arbitrary angles, configurable centre, optional canvas expansion, interpolation,
  and shared border policy.

### Batch 2 — Image adjustments

- Brightness, Contrast, Clamp, Colour Levels, Hue, Saturation, Invert Colour, Opacity, Stretch
  Contrast, and Gamma implement the architecture's complete adjustment set.
- Add Scalar, Multiply Scalar, and Divide Scalar implement the section 16.4 image arithmetic nodes
  that the packet's abbreviated list did not repeat.
- Descriptor-backed channel selection consistently distinguishes colour-only processing from alpha;
  finite propagation, rejection, sanitation, and reduction behavior is explicit per node.

### Batch 3 — Image filters

- Gaussian Blur and Sharpen use validated OpenCV kernels and shared border conversion.
- Add Noise has deterministic seed/animation semantics without Python pixel loops.
- Posterize supports explicit levels, optional input clamping, and descriptor-selected channels.

### Batch 4 — Analysis filters

- Threshold operates on channels with OpenCV-compatible modes.
- Canny performs explicit luminance conversion and emits an immutable float32 binary channel.
- Convolve validates finite odd kernels up to 15×15 and supports normalization, scale, and delta.
- Dilate and Erode expose shape, dimensions, iterations, anchor, border, and optional alpha policy.
- High Pass and Low Pass share the Gaussian/border implementation and preserve descriptors.

### Batch 5 — Compositing and channels

- Blend Images validates equal shape, source clock, descriptor compatibility, and optional mask
  compatibility before straight-alpha compositing.
- Separate Channels emits descriptor-backed immutable 2D views and `NoData` for unavailable outputs.
- Combine Channels requires descriptor-defined channel count/semantics and never guesses unlabeled
  channel meaning.
- Image to Luminance ignores alpha, preserves the source clock, and uses explicit colour conversion.

### Batch 6 — Scalar bridges and channel display

- Channel Statistics exposes mean, minimum, maximum, median, sum, and percentile reductions with an
  explicit non-finite policy.
- Remap Number supports arbitrary source/output ranges and optional clamping.
- Float to Integer provides round, floor, ceil, and truncate modes and rejects non-finite input.
- Channel Display publishes only a bounded sanitized uint8 preview; full float32 channel data remains
  engine-local.

### Batch 7 — Hold Image

- Hold Image retains immutable frame references in `deque(maxlen=delay_frames)`, emits `NoData` until
  capacity is reached, and outputs the oldest retained reference before appending the current frame.
- The node validates `image.data.nbytes * delay_frames` against its configured MiB limit and exposes
  compact `NodeMemoryDiagnostic` values rather than frame data.
- Source-component reset, runtime close, state-significant parameter replacement, and descriptor or
  shape changes release retained references.
- The compact memory query completed engine protocol version 6.

## Catalogue architecture and compatibility

- `nodes/image/utilities.py` owns Change Colour Space runtime/definition identity.
- `nodes/image/catalogue.py` composes dimensions, adjustments, filters, utilities, channels, and
  temporal definitions in stable 34-item display order.
- `nodes/image/core.py` remains a compatibility facade. Existing imports resolve to the exact same
  runtime and factory objects rather than wrappers or duplicated definitions.
- `nodes/image/__init__.py` exports the catalogue factory, utility factory, and prior public runtime
  identities.
- `NodeRegistry.definitions()` continues to expose its established lexical type-ID order. Catalogue
  display order and registry enumeration order are intentionally tested as separate contracts.

## Shared policies and interfaces

- `ImageFrame.data` outputs are read-only, C-contiguous, three-dimensional float32 arrays;
  `ChannelFrame.data` outputs are read-only two-dimensional float32 arrays.
- Processing is Qt-free and vectorized through NumPy/OpenCV. There are no Python pixel loops.
- Input arrays are never mutated. Metadata, source clock, context, and provenance are preserved unless
  a node contract explicitly transforms them.
- Scheduler-owned `NoData` propagation remains the default. Recoverable malformed input/parameter
  cases raise `ExpectedNodeError`, which the Scheduler translates to `NodeExecutionError` and
  `NoData` outputs.
- Multi-input image/channel operations require compatible dimensions and source clocks; they never
  resize, synchronize, infer descriptors, or convert colour implicitly.
- Alpha and non-finite behavior is fixed per definition in
  [`phase-5-node-matrix.md`](phase-5-node-matrix.md).
- Hold Image reset ownership remains `Scheduler.reset_source(clock_id, reason)`, preserving the
  source-component lifecycle boundary established in earlier phases.

## Persisted acceptance graphs

[`examples/phase5`](../examples/phase5) contains two schema-versioned graphs and deterministic media.

### Executable reference graph

`reference_graph.synmachine.json` production-loads and compiles as nine nodes:

```text
Load Video → Resize 500×500 → Gaussian Blur 5×5 → Change Colour Space (HSV)
    → Separate Channels → channel_1 (Hue) → Channel to Pitch → Note Visualizer

HSV image → Canny → Channel Display
```

The source path is relative and resolves through production persistence to the included generated MP4.
It replaces the architecture's future Synthetic/Camera source with deterministic Load Video because
Synthetic is not implemented and Phase 6 is outside this work. The graph has no camera, audio, or MIDI
hardware sink.

### All-definition catalogue smoke

`catalogue.synmachine.json` contains every production registry definition exactly once: 51 nodes with
unique stable IDs and default parameters. It production-loads without unknown type IDs or unsupported
versions and round-trips canonically through the persistence boundary.

The catalogue is intentionally disconnected. It is a load/instantiation and browsable-reference
artifact, not a runnable graph. Compilation is expected to report only missing required inputs and
unresolved generic types; tests reject any other structural issue.

## Exit-criteria evidence

| Phase 5 criterion | Evidence |
| --- | --- |
| Every node meets the definition of done | Seven immutable batch suites cover algorithms, definition metadata, parameter validation, common conformance, `NoData`, immutability, alpha, and non-finite policies. |
| Catalogue smoke can instantiate every definition | The persisted 51-node catalogue production-loads, matches the exact registry sequence, has one instance per type ID, and round-trips canonically. |
| No input mutation is detected | Shared conformance snapshots inputs and asserts read-only, unchanged arrays across image/channel algorithms; the complete Phase 5 suite passes. |
| Memory remains bounded across a 30-minute looping graph | The canonical accelerated report executes 108,000 production Scheduler ticks, simulates 180 loop resets, proves exact Hold Image capacity, and passes the sampled RSS gate. |
| Earlier phases remain operational | The complete 599-test suite and production offscreen smoke retain all Phase 1–4 behavior. |

## Tests and results

Final acceptance was run on Windows 11 x64 with CPython 3.12.13:

| Command | Result |
| --- | --- |
| `uv run check` | Passed; Ruff format/lint clean and strict Pyright reported 0 errors, warnings, or information messages. |
| `uv run pytest -q tests/phase5` | Passed; 299 Phase 5 tests. |
| `uv run pytest -q` | Passed; 599 tests including all Phase 1–4 and smoke regressions. |
| `$env:QT_QPA_PLATFORM='offscreen'; uv run synmachine --smoke-test` | Passed; production application composition started and stopped normally. |
| `uv run python -m tools.phase5_soak --output docs/phase-5-soak.json` | Passed; 108,000 ticks, 30.0 equivalent minutes, 180 source-loop resets, and zero scheduler errors. |
| Persisted structural probe | Passed; 51-node catalogue load/round-trip and nine-node executable reference graph compile contracts were verified by production persistence/compiler tests. |
| `git diff --check` | Passed before the final checkpoint; no whitespace errors. |

The 299 Phase 5 tests include batch algorithm cases, shared conformance, registry/catalogue identity and
ordering, persistence round trips, the executable reference topology, Hold Image reset/memory
diagnostics, and a fast bounded-memory tool test. Tests use deterministic arrays, generated media,
mocked services, and offscreen Qt; they require no physical camera, MIDI port, or audio output.

## Bounded-memory observations

Raw evidence is committed as [`phase-5-soak.json`](phase-5-soak.json). The no-argument canonical tool
configuration production-compiles:

```text
Load Video source definition → Hold Image → Display Image Data
```

The tool injects immutable 64×64×3 float32 source frames through `Scheduler.execute_tick()` rather
than opening a decoder, and calls `Scheduler.reset_source(..., SOURCE_RESTARTED)` at each 600-frame
loop boundary. It runs without sleeping, so 108,000 ticks represent 1,800 source-clock seconds at
60 FPS (30 minutes) rather than 30 minutes of wall-clock occupancy.

| Measurement | Result |
| --- | ---: |
| Total ticks / equivalent duration | 108,000 / 30.0 minutes |
| Wall-clock duration | 1.207 seconds |
| Loop length / reset count | 600 frames / 180 resets |
| Scheduler errors | 0 |
| Frame shape / bytes | 64×64×3 / 49,152 bytes |
| Hold delay / exact retained capacity | 8 frames / 393,216 bytes |
| Peak retained frames / bytes | 8 / 393,216 |
| Final retained frames / bytes | 0 / 0 |
| Start / post-warm-up / final RSS | 91,914,240 / 91,975,680 / 91,983,872 bytes |
| Post-warm-up RSS growth / sampled span | 8,192 / 8,192 bytes |
| RSS growth/span limit | 33,554,432 bytes (32 MiB) |
| Samples | 19 (at comparable post-reset lifecycle points) |
| Result | passed |

The exact Hold Image bound is structural: production code retains references in a bounded deque and
the evidence checks initial fill and the full retained state immediately before every loop reset. Each
reset is then required to release all retained references. Process RSS is sampled at comparable
post-reset lifecycle points, so its growth/span measures memory remaining across loops rather than the
expected bounded history. RSS includes Python allocator and native-library behavior visible to the OS
and is allocator- and OS-dependent, so the gate applies a documented post-warm-up growth/span
allowance rather than exact equality.

## Known limitations

- The soak is accelerated source-clock evidence, not 30 minutes of wall-clock waiting. It exercises
  the production compiler, Scheduler, Hold Image runtime, visualizer sink, reset path, and process RSS,
  but intentionally excludes decoder timing, hardware, UI rendering, and inter-process transport.
- `ResetReason.SOURCE_RESTARTED` represents source-loop boundaries because the current reset enum has
  no separate `SOURCE_LOOP` member.
- Process RSS varies by allocator, loaded native libraries, and operating system. The committed value
  is one run on the recorded environment; the pass/fail contract is the bounded 32 MiB allowance.
- The persisted catalogue is intentionally not executable because required inputs are disconnected
  and generic port types cannot resolve without connections.
- The executable reference uses deterministic Load Video rather than the architecture diagram's
  Synthetic/Camera source. Synthetic remains future scope, and no Phase 6 work was pulled forward.
- The reference graph verifies the newly available Gaussian Blur/Canny vertical slice but is not a
  three-run release throughput benchmark and does not claim physical display observation.

## Architecture deviations

No architecture deviation, schema migration, or new dependency was introduced, and no ADR was
required.

The implementation follows the master architecture's immutable frame, explicit descriptor,
scheduler-owned `NoData`, source-reset ownership, vectorized processing, bounded preview, and compact
IPC policies. Splitting Change Colour Space into `utilities.py` and composing image definitions in
`catalogue.py` fulfills the documented module plan while `core.py` preserves exact public identities.

Using deterministic Load Video in the executable example and sampled process RSS in the accelerated
soak are evidence-method choices under the stated limitations, not architecture changes.

## Next work

Phase 5 is complete. The next implementation phase is Phase 6 only; it is intentionally not started
by this checkpoint.