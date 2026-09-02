# AGENTS.md

This file applies to the entire repository. It is the operating guide for coding agents working on
Synesthesia Machine. Keep it current when project-wide commands, architecture, or release practices
change.

## Project in one paragraph

Synesthesia Machine is a desktop instrument for Windows 11 x64 and Linux x64 that turns video or
camera frames into live desired MIDI note states through a typed visual node graph. It is written for CPython 3.12,
managed with `uv`, rendered with PySide6/Qt Widgets, and packaged as a platform-native standalone directory.
The UI owns graph editing and Qt objects; a spawned engine process owns sources, runtime nodes,
full-resolution image data, MIDI output, audio, and profiling. The graph is a directed acyclic graph
with deterministic compilation, source-driven execution, bounded latest-frame transport, and
schema-versioned JSON persistence.

## Start every task here

1. Read the user request and define the smallest coherent scope.
2. Run `git status --short` before editing. The worktree may already contain user changes; preserve
   them and do not reformat, revert, stage, or overwrite unrelated work.
3. Read `README.md`, `docs/agent-playbooks/README.md`, every playbook it routes to for the planned
   change, the local package `README.md` for every package you will touch, the relevant tests, and the
   public package facade (`__init__.py`) before changing an interface.
4. Consult `docs/architecture/master.md` for the architectural baseline and
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

- Supported development and packaging platforms: Windows 11 x64 and Linux x64 (glibc). See
  ADR-0012; macOS is out of scope.
- Shell examples in this repository use PowerShell.
- Supported interpreter: 64-bit CPython 3.12 only. `.python-version` and `pyproject.toml` enforce the
  minor version.
- Dependency and command runner: `uv`. Do not rely on a global Python, a Conda environment, or
  globally installed packages.
- The synchronized virtual environment is the ignored repository-local `.venv`.

From the repository root (identical on both platforms; use PowerShell on Windows and bash on
Linux):

```powershell
uv python install 3.12
uv sync --locked --group dev
```

On a fresh Linux machine also install the Qt shared libraries needed even for the offscreen
platform (`libgl1`, `libegl1`, `libxkbcommon0`, `libfontconfig1`, `libglib2.0-0`) and, for the
optional debug-audio feature, `libportaudio2` (sounddevice resolves PortAudio from the system on
Linux).

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
uv run pytest -q tests/nodes/image/test_filters.py
uv run pytest -q tests/runtime/test_process_engine.py -k handshake
uv run ruff format path/to/changed.py     # format only intentionally changed files
```

For a headless application smoke test, set the Qt platform before the process imports PySide6:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run synmachine --smoke-test
```

```bash
QT_QPA_PLATFORM=offscreen uv run synmachine --smoke-test
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
| `tests/` | Hardware-independent domain, integration, benchmark, packaging, and smoke coverage. Tests use deterministic media, injected capture factories, mock MIDI, direct audio callbacks, and offscreen Qt. |
| `tools/` | Risk probes, deterministic fixture generation, validation, benchmarks, soak tests, evidence generation, and release tooling. Read `tools/README.md` before running them. |
| `examples/` | Current portable graphs, deterministic bundled media, and the generated all-definition catalogue. Historical catalogues are compatibility fixtures under `tests/fixtures/`. |
| `benchmarks/fixtures/` | Deterministic non-user graph inputs owned by performance harnesses. |
| `docs/` | ADRs, phase completion reports, node reference, committed benchmark/evidence JSON, and screenshots. |
| `docs/architecture/` | Active master architecture. Historical delivery packets and completion evidence live under `docs/history/v0-development/`. |
| `packaging/` | Locked standalone builds (`build.ps1`/`smoke_test.ps1` for Windows, `build.sh`/`smoke_test.sh` for Linux), clean-machine smoke procedures, notices, provenance, and release gates. |
| `SynesthesiaMachine.py` | Packaging entry script. Development should normally use `uv run synmachine`. |

## Architectural rules that must not be weakened

### Dependency direction

- `contracts` is the lowest layer. It may use the standard library and NumPy value representation,
  but it must not import `app`, `graph`, `nodes`, `persistence`, `runtime`, or `ui`.
- `graph`, `nodes`, `runtime`, and `persistence` are headless. They must not import PySide6 or
  `synesthesia_machine.app`. `tests/architecture/test_boundaries.py` enforces this.
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
- Preserve `spawn` start-method compatibility on Windows and POSIX: process entry points and transferred values must be
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

Detailed procedures live in `docs/agent-playbooks/`. Read the routing index and every applicable
playbook before editing. A task may require several playbooks; package boundaries do not imply that
only one applies.

### External playbook loading

OpenCode does not automatically expand referenced instruction files. When this file routes a task to
`docs/agent-playbooks/*.md`, use the file-reading tool to load every applicable playbook before
editing. Treat loaded playbooks as mandatory instructions. Load only task-relevant playbooks, but
follow their references when required.

### Mandatory cross-cutting change audit

Before editing a change that spans more than one of graph models, persistence, runtime, engine IPC,
or UI, write a short impact matrix in working notes or commentary. It must identify:

- the authoritative owner of each changed value and every consumer;
- every explicit constructor, clone, `dataclasses.replace`, remap, serializer, migration, IPC payload,
  undo command, cache, and view-model/UI projection affected by changed fields;
- lifecycle behavior on successful activation, rejected activation, graph replacement, restart,
  shutdown, and failure where applicable;
- cardinality changes and their effect on bounds, caches, counters, metrics, deterministic ordering,
  fan-out, and backpressure;
- compatibility requirements for saved graphs, clipboard fragments, examples, and the engine protocol;
- architecture text or accepted ADRs affected by the behavior.

Search the repository for the changed type, field, stable ID, and old assumptions before coding. When
unchanged model fields should survive reconstruction, prefer `dataclasses.replace()` or a centralized
conversion helper over manually repeating every field.

Do not treat a green component test as proof of integration correctness. Add at least one adversarial
cross-layer test for a cross-cutting change. Relevant cases include multiple simultaneous producers,
fan-out, hidden or absent consumers, stale-state cleanup, rejected replacement, legacy serialized
input, and behavior above previous fixed limits.

After implementation and normal gates, perform a distinct review pass against the impact matrix and
the complete diff. For substantial model + persistence + runtime/IPC + UI changes, an independent
fresh-context review is strongly preferred before merge; if it is unavailable, state that limitation.

### Architecture delta gate

Before implementation, compare intended behavior with the master architecture and accepted ADRs.
Changes to ownership, routing, cadence, bounds, persistence semantics, demand roots, process
placement, or lifecycle policy are architectural unless the documents already describe them. Add or
supersede an ADR in the same change; do not defer it until after the implementation.

## Test selection guide

Read `docs/agent-playbooks/testing.md` for every code or test change. Use the narrowest relevant tests
while iterating, then widen according to risk and run the final gates in the definition of done.

## Tools and generated evidence

Read `docs/agent-playbooks/tools-evidence.md` and `tools/README.md` before executing a repository tool,
diagnostic, benchmark, evidence generator, hardware probe, or release command.

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
- The application owns logs and recovery under `%LOCALAPPDATA%\SynesthesiaMachine` (Windows) or
  `$XDG_DATA_HOME/SynesthesiaMachine` (Linux); user graph paths are separate and must never be
  removed as cleanup.

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
