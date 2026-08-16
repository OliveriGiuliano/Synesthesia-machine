# AGENTS.md

This file applies to the entire repository. It is the operating guide for coding agents working on
Synesthesia Machine. Keep it current when project-wide commands, architecture, or release practices
change.

## Project in one paragraph

Synesthesia Machine is a Windows 11 x64 desktop instrument that turns video or camera frames into
live desired MIDI note states through a typed visual node graph. It is written for CPython 3.12,
managed with `uv`, rendered with PySide6/Qt Widgets, and packaged as a standalone Windows directory.
The UI owns graph editing and Qt objects; a spawned engine process owns sources, runtime nodes,
full-resolution image data, MIDI output, audio, and profiling. The graph is a directed acyclic graph
with deterministic compilation, source-driven execution, bounded latest-frame transport, and
schema-versioned JSON persistence.

## Start every task here

1. Read the user request and define the smallest coherent scope.
2. Run `git status --short` before editing. The worktree may already contain user changes; preserve
   them and do not reformat, revert, stage, or overwrite unrelated work.
3. Read `README.md`, the local package `README.md` for every package you will touch, the relevant
   tests, and the public package facade (`__init__.py`) before changing an interface.
4. Consult `synesthesia_machine_design/00_master_architecture.md` for the architectural baseline and
   the accepted records under `docs/adr/` for binding decisions. A deliberate architectural
   deviation requires a new ADR before implementation.
5. Treat phase packets and completion reports as design history and acceptance evidence, not as a
   substitute for inspecting the current code. When documentation and implementation appear to
   disagree, resolve the mismatch explicitly; do not silently choose one.
6. Make focused changes and add or update tests with every behavioral change.
7. Run targeted tests while iterating, then the repository gates appropriate to the final change.
8. Review `git diff --check`, `git diff --stat`, and the actual diff before handing off.

Do not create commits, tags, release archives, or distribution artifacts unless the user explicitly
asks for them.

## Supported environment and setup

- Supported development and packaging platform: Windows 11 x64.
- Shell examples in this repository use PowerShell.
- Supported interpreter: 64-bit CPython 3.12 only. `.python-version` and `pyproject.toml` enforce the
  minor version.
- Dependency and command runner: `uv`. Do not rely on a global Python, a Conda environment, or
  globally installed packages.
- The synchronized virtual environment is the ignored repository-local `.venv`.

From the repository root:

```powershell
uv python install 3.12
uv sync --locked --group dev
```

`uv.lock` is part of the reproducibility contract. If a dependency change is truly necessary,
explain why the standard library and current stack are insufficient, update `pyproject.toml` and
`uv.lock` together, and consider packaging and license consequences. Never casually widen the
supported Python or native-library versions.

## Everyday commands

Run all Python entry points through `uv` and from the repository root.

```powershell
uv run synmachine                         # open the application
uv run synmachine --smoke-test            # launch and close the real application shell
uv run check                              # Ruff format check, Ruff lint, strict Pyright
uv run test                               # complete pytest suite
uv run pytest -q                          # the test command used by CI
uv run pytest -q tests/phase5/test_batch3_filters.py
uv run pytest -q tests/phase4/test_process_engine.py -k handshake
uv run ruff format path/to/changed.py     # format only intentionally changed files
```

For a headless application smoke test, set the Qt platform before the process imports PySide6:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run synmachine --smoke-test
```

`uv run check` does not format files for you; it runs `ruff format --check`, `ruff check`, and strict
Pyright. Prefer formatting only the files in scope instead of mechanically rewriting the repository.

## Repository map

| Path | Responsibility and constraints |
| --- | --- |
| `src/synesthesia_machine/app/` | Composition root, entry point, application paths, logging, and packaged self-test. `bootstrap.py` owns `freeze_support()` and application assembly. |
| `src/synesthesia_machine/contracts/` | Lowest-level immutable runtime values and versioned client/process contracts. No Qt or imports from implementation packages. Use the public facade. |
| `src/synesthesia_machine/graph/` | Qt-free authoring model, validation, layout, random graph operations, and deterministic compilation. |
| `src/synesthesia_machine/nodes/` | Node metadata, runtime protocols, registry, and built-in node implementations grouped by domain. No UI imports. |
| `src/synesthesia_machine/media/` | Reusable NumPy/OpenCV/PyAV image, file-source, and camera behavior. Keep algorithms out of the UI. |
| `src/synesthesia_machine/midi/` | Scale logic, exact-port MIDI output lifecycle, and the debug synthesizer. Backends must remain mockable. |
| `src/synesthesia_machine/runtime/` | Execution plans, scheduler, engine facade/client/server, bounded source mailboxes, shared previews, and profiling. It is Qt-free. |
| `src/synesthesia_machine/persistence/` | Strict graph JSON conversion, pure sequential migrations, atomic save/backup, clipboard, recovery, and media relinking. |
| `src/synesthesia_machine/ui/` | PySide6 editor, view models, undo commands, canvas, inspectors, previews, profiler, transport, settings, themes, and translations. |
| `src/synesthesia_machine/diagnostics/` | Hardware summaries, structured logs, and bounded/redacted diagnostic bundles. Never include frame pixels or unapproved personal paths. |
| `tests/` | Hardware-independent phase and smoke coverage. Tests use deterministic media, injected capture factories, mock MIDI, direct audio callbacks, and offscreen Qt. |
| `tools/` | Risk probes, deterministic fixture generation, validation, benchmarks, soak tests, evidence generation, and release tooling. Read `tools/README.md` before running them. |
| `examples/` | Persisted portable graphs and deterministic bundled media. Phase 6 contains the current generated all-definition catalogue; earlier catalogues may be historical acceptance artifacts. |
| `docs/` | ADRs, phase completion reports, node reference, committed benchmark/evidence JSON, and screenshots. |
| `synesthesia_machine_design/` | Master architecture and historical implementation phase packets. |
| `packaging/` | Locked standalone build, clean-machine smoke procedure, notices, provenance, and release gates. |
| `SynesthesiaMachine.py` | Packaging entry script. Development should normally use `uv run synmachine`. |

## Architectural rules that must not be weakened

### Dependency direction

- `contracts` is the lowest layer. It may use the standard library and NumPy value representation,
  but it must not import `app`, `graph`, `nodes`, `persistence`, `runtime`, or `ui`.
- `graph`, `nodes`, `runtime`, and `persistence` are headless. They must not import PySide6 or
  `synesthesia_machine.app`. `tests/phase1/test_boundaries.py` enforces this.
- `graph` may consume public contracts, immutable node definitions, and execution-plan value types;
  it must not depend on the scheduler, persistence, Qt, or concrete application composition.
- Concrete nodes may depend on contracts plus reusable media/MIDI services, never on UI classes.
- `persistence` serializes domain values, never live runtime instances, device handles, frames,
  metrics, or Qt state.
- `ui` may coordinate graph, persistence, and the `EngineClient` API, but image algorithms and device
  work belong outside the UI package.
- Import supported cross-package APIs from package facades where they exist. Do not solve circular
  dependencies with opportunistic local imports. The deliberate lazy exports in `runtime.__init__`
  are part of the existing architecture, not a general pattern to spread.

### Process boundary and concurrency

- All Qt widgets, graphics items, `QImage`, and `QPixmap` objects stay in the UI process.
- Video decoding, camera capture, full-resolution arrays, node runtimes, MIDI, audio, and profiling
  stay in the engine process.
- Do not send live NumPy image arrays through ordinary multiprocessing queues. Control/events use
  versioned dataclasses; bounded shared memory carries throttled preview bytes.
- Preserve Windows `spawn` compatibility: process entry points and transferred values must be
  importable/picklable, module import must not start work, and the application entry point must keep
  `freeze_support()`.
- The system prefers recent data and bounded latency over processing every live frame. Do not turn a
  latest-frame mailbox into an unbounded queue or retain stale frames to make throughput figures look
  better.
- Scheduler and engine cleanup must remain idempotent after normal shutdown, failed startup, and
  forced child termination. Plan replacement is prepared and committed atomically; a bad candidate
  must not destroy the working plan.
- Never access a Qt widget from an engine thread or child process. Do not block the UI event loop with
  decoding, graph execution, device I/O, or long diagnostics.

### Graph and runtime semantics

- Ordinary connections form a DAG. Stateful behavior belongs in explicit stateful nodes, not graph
  feedback cycles.
- An ordinary input accepts at most one incoming connection; outputs may fan out without copying or
  re-executing the producer.
- `INT -> FLOAT` is the only implicit concrete conversion. Image color conversion, image/channel
  conversion, and float-to-int policies remain explicit.
- Multiple dynamic inputs must belong to the same source clock unless a node explicitly defines a
  supported composition policy. Preserve `FrameContext` and source clock identity through derived
  values.
- Demand roots determine execution. Each demanded node executes at most once per source tick. Static
  subgraphs may be cached; stateful/source nodes must use the correct cache and reset policies.
- Use the `NoData` singleton for a temporarily unavailable connected value. Do not overload `None`.
  Unless `handles_no_data=True`, the scheduler skips the runtime and publishes `NoData` outputs.
- Recoverable, user-facing runtime failures should raise `ExpectedNodeError` with a stable code and a
  clear message. Do not hide unexpected failures with blanket exception handling; the scheduler
  boundary records them as unexpected node errors.
- Runtime objects must implement the full `process`, `reset`, and `close` lifecycle. Stateful nodes
  must release retained frames and device state for every relevant `ResetReason`.

### Runtime data invariants

- `ImageFrame.data` is C-contiguous, read-only `numpy.float32`, shaped `H x W x C`, and accompanied by
  explicit color-space, channel-name, alpha, context, and provenance metadata.
- `ChannelFrame.data` is read-only `numpy.float32`, shaped `H x W`, with semantic, nominal range,
  cyclic flag, and context metadata.
- Treat every input array as immutable because one output may feed many consumers. Never mutate an
  input in place. Use `read_only_float32()` at runtime boundaries and preserve metadata intentionally.
- Prefer vectorized NumPy, OpenCV, PyAV/FFmpeg, or another already approved native operation. Python
  per-pixel loops are prohibited.
- Define and test behavior for NaN/infinity wherever an algorithm may receive or produce them.
  Display and MIDI conversion paths must sanitize unsuitable values.
- MIDI graph values are immutable desired note states, not repeated raw `note_on` events. Only output
  services compare desired/current state and emit note-on, note-off, or panic messages.

## Change playbooks

### Adding or changing a node

1. Inspect `nodes/base.py`, a neighboring implementation, its package `__init__.py`, and the relevant
   conformance tests before coding.
2. Put reusable image/math behavior in `media/` or another headless helper when it is not inherently
   node-specific. Keep the node runtime as orchestration and contract enforcement.
3. Define immutable metadata with a stable namespaced `type_id`, stable input/output/parameter IDs,
   an `implementation_version`, explicit execution/cache behavior, validated defaults, and a runtime
   factory. Search the complete registry before selecting an ID.
4. Register the definition through the relevant `create_*_definitions()` or utility registry factory.
   `nodes/composition.py` is the headless built-in composition point; `app/registry.py` is only its
   application-facing compatibility name.
5. Test defaults and invalid parameters, nominal behavior, `NoData`, non-finite values, metadata and
   immutability, dynamic type/clock resolution, state reset/close, compilation, and persistence as
   applicable. Image/channel nodes should reuse the Phase 5 conformance helpers where suitable.
6. If persisted behavior changes, preserve IDs. Bump `implementation_version` and add a pure,
   sequential node migration when an old payload needs transformation. Do not put compatibility
   branches into the runtime.
7. Update the appropriate node/reference documentation and an example or catalogue acceptance
   artifact. The current disconnected catalogue is generated deterministically with:

   ```powershell
   uv run python -m tools.generate_catalogue
   ```

   This command overwrites `examples/phase6/catalogue.synmachine.json`. Run it only when the built-in
   registry intentionally changes. Do not rewrite a historical phase catalogue unless the task
   explicitly calls for it.

### Changing graph models, compilation, or scheduling

- Keep authoring models Qt-free and snapshots immutable. Mutations belong on `GraphDocument` and must
  advance its revision exactly when state changes.
- Validation should produce stable, navigable issue codes rather than UI dialogs or generic strings.
- Compilation must be deterministic: stable ordering, type resolution, cycle rejection, clock-domain
  analysis, demand reachability, and execution-plan construction should not depend on dict/set order.
- Preserve atomic live-edit behavior. Invalid graph revisions remain editable in the UI but must not
  replace the last valid active runtime.
- When changing state-retention keys or plan replacement, test both reusable and invalidated runtime
  paths, including factory/reset/close failures.
- Add focused Phase 1 tests and relevant in-process/process integration coverage. Scheduler changes
  often also require Phase 3, Phase 4, Phase 5 conformance, and Phase 8 profiling checks.

### Changing persisted graph data

- `.synmachine.json` is strict, deterministic UTF-8 JSON with sorted content and a final newline.
- Do not rename or repurpose persisted type, port, parameter, document-setting, or field IDs.
- A graph container change requires incrementing `GRAPH_SCHEMA_VERSION` and adding a pure one-version
  migration in `persistence/schemas.py`. A single node payload change normally requires a node
  implementation-version migration instead.
- Migrations must deep-copy input, be deterministic, move exactly one version, preserve node identity
  and type, and reject unsupported future versions.
- Keep parsing strict and errors structured through `GraphPersistenceError`; malformed or newer files
  must never crash the application.
- Preserve atomic save semantics: fsync a same-directory temporary, retain one `.bak` previous
  generation for explicit saves, and replace atomically. Autosaves intentionally do not create backup
  chains.
- Persist portable media paths relative to the graph directory when possible and resolve them at load.
  Keep size/fingerprint relink checks; never silently substitute a different media file.
- Add round-trip, deterministic serialization, migration fixture, malformed input, backup, and example
  validation tests. Run the read-only graph validator after changing persistence:

  ```powershell
  uv run python -m tools.phase7_validate_graphs examples tests/fixtures/phase7
  ```

### Changing engine IPC or lifecycle

- Inspect the public `EngineClient` protocol in `contracts/engine_client.py`, message dataclasses and
  unions in `contracts/engine_messages.py`, and both client/server implementations.
- If the wire contract changes, increment `ENGINE_PROTOCOL_VERSION` and update exports, message unions,
  handshake behavior, protocol mismatch handling, client, server, mocks, and spawn-process tests as one
  atomic change.
- Commands and responses should retain request IDs and graph revisions where relevant. Async events
  must be safe to ignore when stale.
- Test happy path, timeout, mismatch, child crash, restart, shutdown, shared-memory cleanup, stale
  revision, and double-close behavior as applicable.
- Preserve best-effort MIDI note-off/panic and device cleanup on stop, reload, port change, graph
  replacement, engine failure, and application close.

### Changing the Qt editor

- User-visible graph mutations go through `DocumentSession` and focused `QUndoCommand` classes. Do not
  mutate the document directly from widgets in a way that bypasses undo/redo, dirty state, autosave,
  validation, scene synchronization, or runtime activation.
- Keep domain logic in graph/persistence/runtime services and projection logic in view models. Widgets
  should not become a second source of graph truth.
- Preserve incremental scene synchronization, lazy parameter editors, low-zoom detail suppression,
  and stable graphics-item identity; these are measured large-graph behaviors.
- Use the central action registry for commands and shortcuts. Give interactive controls useful object
  names, accessible names, status tips, and tooltips where neighboring code does so.
- All authored visible English strings must pass through `ui.translations.tr()` or `trf()`, have a
  French entry when the feature is user-visible, and refresh through the relevant `retranslate()`
  path after a language switch.
- Qt tests must create one application, process deferred deletion, close test-owned top-level widgets,
  and inject temporary `QSettings` and `ApplicationPaths`. Do not write tests against the developer's
  real registry settings, logs, recovery directory, clipboard contents, or home directory.
- Set `QT_QPA_PLATFORM=offscreen` before importing PySide6 in headless test modules.

### Changing media, MIDI, audio, or diagnostics

- File video uses PyAV timestamps; camera capture and image operations use the approved OpenCV/NumPy
  stack. Preserve source lifecycle, bounded queues, deterministic generated fixtures, and exact
  metadata semantics.
- Physical device absence is a normal unavailable state. Never select a fallback camera, MIDI port,
  or output device when the user requested an exact identity.
- Automated tests must not open a physical camera, send MIDI, or play audio. Inject capture factories,
  use `MockMidiBackend`, and call audio callbacks with preallocated arrays.
- Hardware commands are opt-in. `tools.camera_probe` opens cameras, `tools.audio_probe --play` emits
  sound, and `tools.phase4_midi_evidence` sends real MIDI only after two exact matching port arguments.
- Diagnostic bundles are bounded, redact filesystem paths by default, exclude frame/image pixels, and
  include paths only with explicit user consent. Never add credentials, environment secrets, or raw
  user media to logs or evidence.
- Performance changes need correctness tests first and measurements second. Do not make performance
  claims from one noisy run, change a gate to bless a regression, or round a failing result into a
  pass. ADR-0007 documents the accepted Phase 8 throughput interpretation.

### Changing packaging or versions

- The source version in `src/synesthesia_machine/version.py`, project version in `pyproject.toml`, and
  product/file versions in `pysidedeploy.spec` must agree. Use
  `uv run python -m tools.phase9_release check` to verify them.
- Read all of `packaging/README.md`, `packaging/release-checklist.md`, and
  `packaging/licensing-review.md` before release work.
- `packaging/build.ps1` is not an ordinary test command. It synchronizes packaging dependencies,
  runs gates, deletes and recreates repository-owned `packaging/out`, `packaging/work`, and
  `deployment`, and refuses a dirty tree unless `-AllowDirty` is passed. An `-AllowDirty` or
  `-SkipTests` artifact is never a release candidate.
- The default release is a standalone directory, not one-file packaging or an installer. Preserve the
  native runtime checks, ASIO exclusion, notices/licenses, deterministic archive ordering/timestamps,
  provenance, and clean-machine smoke gate.
- The repository does not yet declare an approved application license or distribution model. Do not
  publish or represent an artifact as legally cleared. Dependency/codec and third-party notice review
  remain release blockers until explicitly approved.
- Never delete user graph documents or `%LOCALAPPDATA%\SynesthesiaMachine` during packaging, rollback,
  uninstall, or smoke testing.

## Test selection guide

Use the narrowest relevant tests during development, then widen coverage according to risk.

| Area changed | Start with |
| --- | --- |
| Runtime values, definitions, graph, compiler, scheduler, persistence boundaries | `tests/phase1/` |
| Editor shell, commands, clipboard, autosave, session | `tests/phase2/` |
| Video vertical slice, previews, debug synth, in-process client | `tests/phase3/` |
| Spawned engine, plan swap, camera, MIDI, shared memory, supervision | `tests/phase4/` |
| Image/channel nodes, immutability, catalogue, soak behavior | `tests/phase5/` |
| Synesthesia algorithms, MIDI utilities, examples, benchmarks | `tests/phase6/` |
| Groups/layout, settings, recovery, relinking, migrations, large graph | `tests/phase7/` |
| Profiling, diagnostics, benchmark/soak harnesses, native-thread behavior | `tests/phase8/` |
| Release configuration, packaged smoke, archive reproducibility | `tests/phase9/` |
| Dependency/environment adapters and bootstrap smoke | `tests/smoke/` |
| Cross-phase requested behavior | Root-level `tests/test_*.py` files |

Testing rules:

- Use exact equality for discrete graph and MIDI results where possible. Use
  `numpy.testing.assert_allclose` with a deliberate node-specific tolerance for numeric image work.
- Assert semantic outcomes in addition to shapes or opaque golden data: metadata, ranges, edge
  positions, frequency peaks, note state, lifecycle calls, and source clocks.
- Use deterministic UUIDs, seeds, arrays, generated videos, clocks, and timestamps. Do not make tests
  depend on ordering accidents, wall-clock sleeps, the network, or local hardware.
- Put temporary files under pytest's `tmp_path`. Process tests must clean up clients and shared memory
  in `finally` blocks or fixtures.
- A behavior fix needs a regression test that fails for the original defect. Do not weaken an existing
  assertion merely to make a new implementation pass.
- Run `uv run check` after code changes. Before final handoff, run `uv run pytest -q` unless the task is
  documentation-only or the suite cannot reasonably run; report any unrun gate and why.

CI on `windows-latest` sets `QT_QPA_PLATFORM=offscreen`, installs the locked development group, then
runs `uv run check` and `uv run pytest -q`. Local success on another platform is not evidence that
Windows spawn, devices, Qt deployment, or case-insensitive filesystem behavior is correct.

## Tools and generated evidence

Read `tools/README.md` before executing a diagnostic. Many tools intentionally overwrite committed
evidence when run without `--output`, including phase performance, soak, profiler, and benchmark JSON
or screenshots under `docs/`. Use a path under the system temporary directory for exploratory runs
when the tool supports it, and inspect `git status` immediately afterward.

Important distinctions:

- `tools.phase7_validate_graphs` is read-only unless `--output` is supplied.
- `tools.generate_catalogue` intentionally overwrites the current Phase 6 catalogue.
- `tools.generate_test_video` writes the path supplied by the caller.
- Phase 3/4 performance tools launch Qt and write JSON plus screenshots by default.
- Phase 5/6/8 evidence tools write tracked `docs/` outputs by default and may be long-running.
- Hardware probes may enumerate or open real devices; only run the explicitly invasive modes with
  user authorization and an exact target.
- Release scripts generate or replace packaging artifacts and must be treated as release operations.

Never edit measured evidence by hand to manufacture a pass. If an intentional canonical run updates
evidence, keep the environment, dependency versions, Git state, parameters, and pass/fail result
truthful and update the corresponding report when required.

## Style and implementation conventions

- Follow Ruff's 100-character line limit and enabled rules in `pyproject.toml`.
- Application and tool code is checked by strict Pyright. Prefer precise types, immutable frozen/slotted
  dataclasses for values, `Mapping`/`Sequence` interfaces, `StrEnum` for serialized enums, and narrow
  validation helpers consistent with neighboring code.
- Use `from __future__ import annotations` in modules that need postponed annotations, following the
  surrounding package style.
- Keep stable public APIs in package `__init__.py` facades and update `__all__` when an intended public
  symbol changes.
- Use descriptive names and small explicit functions. Do not introduce a framework or abstraction to
  save a few lines, use `Any` to suppress an architectural problem, or add blanket type-ignore rules.
- Narrow `# type: ignore[...]` comments are acceptable only at an actual incomplete third-party typing
  boundary and should explain themselves when the reason is not obvious.
- Log through the existing `synesthesia_machine.ui` and `synesthesia_machine.engine` structured
  logging paths. Do not log frame data, credentials, or unredacted private paths.
- Preserve deterministic ordering in registries, graph serialization, compiler output, reports, and
  archives. Sort by stable IDs or explicit keys rather than relying on incidental container order.
- Comments should explain lifecycle, safety, numerical, or architectural reasons, not restate syntax.

## Worktree and file safety

- Assume every pre-existing modification or untracked file belongs to the user. Work around it and
  mention overlaps instead of reverting it.
- Do not use destructive Git commands, broad cleanup commands, or repository-wide mechanical rewrites.
- Do not hand-edit `uv.lock`, generated catalogue JSON, benchmark evidence, dependency inventories, or
  build provenance. Use their owning commands and review the result.
- Keep local output out of the repository when possible. Ignored locations include `.venv`, `logs`,
  `diagnostics`, `build`, deployment intermediates, and packaging outputs, but ignored does not mean
  disposable when it may contain user evidence.
- Graph saves intentionally create a sibling `.bak`. Tests and tools should keep such outputs in
  temporary directories unless the task is specifically about a checked-in example.
- The application owns logs and recovery under `%LOCALAPPDATA%\SynesthesiaMachine`; user graph paths
  are separate and must never be removed as cleanup.

## Definition of done

A change is ready to hand off when all applicable statements are true:

- The implementation matches the requested scope and respects the architectural boundaries above.
- Existing serialized IDs, schemas, engine protocol, and user data remain compatible, or explicit
  versioned migrations/protocol changes and tests are included.
- New behavior has focused regression tests, including failure and lifecycle paths where relevant.
- Public exports, French UI translations, package docs, node reference, examples, and generated
  catalogues/evidence are updated where the change requires them.
- Relevant targeted tests pass.
- `uv run check` passes for code changes.
- `uv run pytest -q` passes before a normal code handoff, or the exact reason it was not run is stated.
- A real application smoke, hardware probe, performance run, or packaged smoke is run only when its
  risk and side effects are relevant and authorized; the result and environment are reported plainly.
- `git diff --check` is clean, the final diff contains no unrelated edits, and `git status --short`
  shows only intended new work plus clearly identified pre-existing changes.

In the final report, lead with what changed and the observable outcome. List tests and checks actually
run, note any gate not run, identify migrations or architecture decisions, and call out remaining
limitations without claiming hardware, performance, packaging, or legal validation that did not
occur.
