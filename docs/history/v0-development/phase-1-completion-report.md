# Phase 1 completion report

## Status

Phase 1 — Contracts, graph model, and headless compiler is complete on Windows 11
x64 using the project-local, uv-managed CPython 3.12 environment.

The implementation checkpoints are:

- `2961613` — `feat: add phase 1 graph compiler and runtime`
- `10bd7ba` — `feat: add versioned graph persistence`

## Implemented

- Added immutable runtime contracts for frame context and provenance, image/channel
  arrays, normalized colours, MIDI state, all eight concrete port types, and the
  singleton `NoData` sentinel.
- Enforced float32/read-only array boundaries and immutable mappings for MIDI notes,
  compiled plan data, runtime inputs, and tick results.
- Added stable node, port, parameter, and type-variable contracts plus a deterministic
  node-definition registry.
- Added a Qt-independent mutable `GraphDocument` and immutable `GraphSnapshot`, with
  deterministic UUID ordering, revision tracking, and one-connection input replacement.
- Added structured validation and deterministic compilation for node/type/version
  lookup, parameters, ports, cardinality, concrete compatibility, generic unification,
  cycles, required inputs, clock domains, demand roots, and topological ordering.
- Limited implicit conversion to compiler-owned `INT -> FLOAT` widening. Pass Through
  and Conditional generics must resolve to concrete types at compile time.
- Added immutable execution plans with compiled input bindings, resolved port types,
  clock IDs, static/dynamic classification, and demand-pruning metadata.
- Added a deterministic sequential scheduler with per-tick values, static caching,
  one invocation per demanded node per tick, `NoData` propagation, expected and
  unexpected structured errors, output-less sink execution, and timing hooks.
- Added an in-process `EngineFacade` that compiles before activation, invalidates caches
  on successful plan replacement, and keeps the previous valid plan after a failed
  compilation.
- Added Number, Pass Through, Conditional, Compare, Logic Operation, and scalar Math
  nodes under stable versioned type IDs.
- Added schema version 1 graph persistence with deterministic JSON, strict field/type
  validation, registry-backed node type/version checks, structured colour literals,
  pure sequential migration from the prototype shape, atomic replacement, and one
  `.bak` generation.
- Added package READMEs documenting public facade imports and dependency direction for
  contracts, graph, nodes, runtime, and persistence.
- Added headless boundary coverage proving Qt and application imports remain outside the
  Phase 1 core packages.

## Public interfaces added or changed

- `synesthesia_machine.contracts`
  - `PortType`, `FrameContext`, `FrameProvenance`, `ImageFrame`, `ChannelFrame`
  - `ColorValue`, `MidiNoteKey`, `MidiStateFrame`, `NoData`, `NoDataType`
  - `ParameterValue`, `RuntimeValue`, `read_only_float32`, `clock_id_of`, `is_no_data`
- `synesthesia_machine.nodes`
  - `NodeDefinition`, `NodeRuntime`, `NodeRegistry`
  - `InputPortSpec`, `OutputPortSpec`, `ParameterSpec`, `TypeVariable`
  - `ExecutionKind`, `CachePolicy`, `ParameterUpdateMode`, `ResetReason`
  - `ExpectedNodeError`, `NodeExecutionError`
- `synesthesia_machine.nodes.utility`
  - `create_utility_registry`
- `synesthesia_machine.graph`
  - `GraphDocument`, `GraphSnapshot`, `NodeModel`, `ConnectionModel`, `LiteralValue`
  - `ValidationIssue`, `ValidationReport`, `ValidationSeverity`
  - `GraphCompiler`, `CompilationResult`, `types_compatible`
- `synesthesia_machine.runtime`
  - `ExecutionPlan`, `CompiledNode`, `InputBinding`, `PortKey`, `ScalarConversion`
  - `Scheduler`, `TickResult`, `EngineFacade`
- `synesthesia_machine.persistence`
  - `GRAPH_SCHEMA_VERSION`, schema type aliases, and migration functions
  - `graph_to_data`, `graph_to_json`, `graph_from_data`, `graph_from_json`
  - `save_graph`, `load_graph`, `GraphPersistenceError`

`EngineFacade` is exposed lazily from the runtime facade to keep the lower-level
execution-plan import direction acyclic.

## Tests and results

Final committed implementation acceptance was run on Windows 11 x64 with CPython
3.12.13:

| Command | Result |
| --- | --- |
| `uv run check` | Passed; 52 files formatted, Ruff clean, Pyright strict with 0 errors/warnings. |
| `uv run test` | Passed; 103 tests in 0.58 seconds. |
| `uv run pytest tests/phase1 -q` | Passed; 93 Phase 1 tests in 0.17 seconds. |
| `uv lock --check` | Passed; 24 packages resolved from the committed lockfile. |
| `uv sync --locked` | Passed; 24 packages checked with no lockfile changes. |
| `git diff --check` | Passed; no whitespace errors. |

Phase 1 coverage includes:

- all 64 concrete source/destination type pairs;
- generic resolution, widening, unresolved variables, and incompatible constraints;
- arbitrary cycles and self-loops;
- ordinary-input cardinality, required ports, unknown references, and invalid dynamic
  parameters returning structured diagnostics;
- deterministic UUID topological ordering, demand roots, and source-clock mismatch;
- read-only arrays/mappings and singleton/pickle behavior for `NoData`;
- fan-out execution once per tick, output-less sinks, static cache reuse and invalidation,
  `NoData` propagation, timing, and expected/unexpected node errors;
- deterministic multi-tick utility execution, including Math domain failures that remain
  within the FLOAT runtime contract;
- deterministic JSON round trips, structured colour values, strict unknown
  schema/type/version handling, pure migration fixtures, atomic save/load/backup, and a
  serialize-load-compile-execute integration path;
- preservation of the previous active plan when replacement compilation fails;
- absence of Qt/application dependencies in the Phase 1 core packages.

## Known limitations

- Phase 1 is intentionally headless and in-process. It does not include the Qt graph
  editor, `EngineClient` process integration, or production video, camera, audio, and
  MIDI source/sink nodes.
- Runtime instances are recreated after each successful `EngineFacade` activation.
  Compatible stateful runtime preservation belongs to the later process/lifecycle work.
- Source values may be supplied externally to `Scheduler`; production source ownership,
  mailboxes, and backpressure are deferred to later phases.
- Generic type variables are intentionally limited to Pass Through and Conditional for
  this packet. Future variadic MIDI generics are not part of Phase 1.
- Schema v1 reserves `groups` and top-level editor `ui_state`; loaders require both to be
  empty until their domain models are implemented.
- Loaded snapshots start at revision 0 because revision is transient authoring/runtime
  invalidation state rather than persisted document content.
- The migration framework includes the prototype v0-to-v1 fixture. Future schema and
  node implementation changes must add explicit migrations before old documents can be
  loaded.

## Architecture deviations

No architecture deviations or new dependencies were introduced. No new ADR was
required. The implementation remains within the Phase 1 allowed paths and keeps Qt,
OpenCV, PyAV, MIDI, audio, and application code outside the graph core.

## Recommended next work

Proceed to the next design packet while preserving the stable Phase 1 contracts:

1. Introduce graph mutation commands and Qt editor integration against
   `GraphDocument`/`GraphSnapshot`, without adding Qt dependencies to the domain model.
2. Connect the in-process `EngineFacade` contract to the process-separated engine/client
   architecture established in Phase 0.
3. Add lifecycle-aware runtime reuse/reset for compatible stateful nodes during atomic
   plan swaps.
4. Implement the first production source and visualization nodes using the immutable
   runtime values and one-clock compilation rules.
5. Extend schema migrations only through pure sequential transformations and keep node
   type/port/parameter IDs stable.
6. Continue locked quality gates, focused regression tests, package boundary checks, and
   meaningful Git checkpoints for each subsequent phase.