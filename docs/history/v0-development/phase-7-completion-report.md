# Phase 7 completion report — Editing productivity and robustness

## Outcome

Phase 7 is complete. The editor is now a safer daily-use technical application: organizational canvas
items and layout operations are undoable, user preferences persist, explicit saves and autosaves are
durable, forced-crash recovery preserves unsaved state and the original path, missing media can be
relinked without editing JSON, legacy graph/node payloads migrate, diagnostics navigate to their graph
objects, transport remains scoped to one source, and a generated 500-node graph meets the documented
interaction gates. Phase 8 was not started.

## Ordered implementation and checkpoints

The packet's required order was preserved in non-amended checkpoints:

| Order | Work | Commit |
| ---: | --- | --- |
| 1 | Persisted groups/comments and exact command semantics | `74290c5ce67540f1008b52827908ab031c936efd` |
| 2 | Align, distribute, and deterministic tidy selection | `f81fe38df7de0cf56329c9015ed2bf8becf46e1c` |
| 3 | Typed preferences, recent files, and grid snapping | `3708fe8260764680a31e19bce45e02c8b32a1b83` |
| 4 | Atomic replacement and one-generation backups | `63e932adf3542d6f0aaada8fc9173b428874a116` |
| 5 | Recovery manifests and forced-crash harness | `f53831bd9cd834e33d690266158d9ee0df6294d4` |
| 6 | Portable media identity and missing-media relink | `cb56e50964111123fd6651e8715e283791989613` |
| 7 | Sequential node migrations, fixtures, and validator | `ae1fa020c74eedaabbc6f9bfdea60a5761e20d62` |
| 8 | Inline help, navigable errors, and source-scoped transport | `39d2dd8f03d805e16b85b17eac85ffe8aefbee26` |
| 9 | Incremental large-graph scene, lazy detail, profiler/evidence | `7b47a8644928e65e9787e390bf9cee162383650b` |
| 10 | Final acceptance, evidence index, and completion report | This final `feat: complete phase 7 integration` checkpoint |

The Phase 6 final checkpoint `4676b7d88e13eea5ecab44cd2fbc310b54ad0e02` remains in the direct
lineage. No earlier checkpoint was amended.

## Implemented editor productivity

### Groups, comments, and layout

- Persisted `GROUP` and `COMMENT` canvas values include UUID, title, text, finite position/positive
  size, and strict `#RRGGBB` color.
- Add, delete, move, and edit operations use complete immutable values on the document-owned undo
  stack. Selection deletion restores exact group and graph state on undo.
- Align left/center/right/top/middle/bottom, equal-gap horizontal/vertical distribution, and tidy use
  Qt-free deterministic layout functions. Each user layout gesture commits one `MoveNodesCommand`.
- Tidy derives stable DAG layers from selected graph connectivity and uses deterministic ID ordering
  for ties and cyclic leftovers.

### Preferences and recent files

- `EditorPreferences` persists the autosave delay, grid-snap enablement, grid spacing, and recent-file
  limit through `QSettings`. Invalid values fall back atomically to typed defaults.
- The default autosave inactivity delay remains the required 60 seconds and is user-configurable from
  10 to 3600 seconds.
- Recent paths are absolute, deduplicated, pruned when missing, bounded by the preference, and can be
  cleared from the menu. Window geometry/state continue to round-trip through the same settings owner.
- Grid snapping converts one completed drag into one exact move command; it does not mutate node
  positions continuously while dragging.

## Persistence and recovery changes

### Explicit saves and backups

`save_graph` writes UTF-8/current-schema text to a same-directory temporary file, flushes and fsyncs
it, atomically replaces the destination, and retains exactly one `<graph>.bak` containing the previous
explicit generation. Backup replacement is also temporary-file plus fsync plus atomic replace. A
simulated final replacement failure leaves the explicit graph and backup readable and removes all
temporary files. Recovery writes explicitly disable backup creation so autosave cadence cannot grow a
backup chain.

### Autosave and forced-crash recovery

Each recovery graph has a separate schema-v1 JSON manifest containing the document UUID, recovery
filename, and resolved explicit graph path. Manifest writes use the same durable atomic helper. Startup
prefers this path over recent-file scanning and offers recovery only when the recovery payload is newer
than the explicit save. Discard and successful explicit save remove payload, manifest, and any obsolete
pre-Phase-7 recovery backup.

The canonical [`phase-7-recovery.json`](phase-7-recovery.json) was produced by spawning a child,
adding an unsaved node, durably autosaving, and calling `os._exit(73)`. The parent observed exit code
73, retained the explicit path, and loaded two nodes from recovery versus one from the explicit graph.
The unsaved node ID was present and the recovery timestamp was newer.

### Portable and missing media

- Load Video paths inside the graph tree persist relative to the graph directory.
- Its node `ui_state` stores the resolved absolute fallback, byte size, and a bounded-cost sampled
  SHA-256 identity (file size plus first/last content samples).
- Moving the whole graph/media tree resolves the relative path at its new location without prompting.
- If media is missing, File → Locate Missing Media selects the chosen Load Video node first, requires
  an explicit file choice, verifies stored size/fingerprint, and warns before accepting a mismatch.
  Same-name files are never substituted silently.
- Relink updates path and identity together as one complete-node undo command; undo/redo restores the
  exact before/after node values.

### Graph and node migrations

The existing pure graph v0→v1 migration remains the current graph-schema chain. Phase 7 adds a
`NodeMigrationRegistry` keyed by `(type_id, from_version)`. Every step receives and returns a deep copy,
must advance exactly one version, and cannot change node identity/type. Built-in fixtures cover:

- pre-versioned graph plus Number v0 `value` → v1 typed number parameters; and
- graph v1 plus Load Video node v0 `path` → v1 `file_path`.

`tools.phase7_validate_graphs` is read-only. It recursively validates `.synmachine.json` files and
reports graph/node migration step counts and actionable loader errors. Final acceptance checked eight
current examples plus both legacy fixtures: 10 valid, 0 invalid, exactly 2 migrated.

The graph schema remains version 1 because Phase 7 uses the schema's already-reserved `groups` and
per-node `ui_state` fields; no incompatible top-level shape was introduced. Historical implementation
payloads are handled by the new node-version chain.

## Help, diagnostics, and transport UX

- The Inspector displays non-empty `ParameterSpec.help_text` beneath its editor as visible wrapped
  text; editor and label tooltips preserve the same metadata.
- Canvas node/cable tooltips include full compiler messages and stable codes. Node badges distinguish
  warnings from errors.
- A persistent Validation Issues dock shows global error/warning counts and detailed tooltips with
  code, node/connection, and port IDs. Activating an issue selects and scrolls to its graph object.
- The status-bar validation summary exposes the first eight issue details without requiring a
  selection.
- A Qt-free transport resolver targets exactly one selected source, otherwise the sole source. It
  refuses multiple selected sources and multiple unselected sources; actions and tooltips state the
  current target or required correction. Panic remains intentionally global.

## Large-graph refinements and evidence

Scene synchronization now diffs immutable view models and replaces only changed/removed graphics
items. An edit therefore retains every unaffected graphics item, selection, and cache. At 200 or more
nodes, embedded parameter editors are lazy and materialize only for a selected node; the Inspector
remains available for all nodes. Below 0.55 zoom, ports/editors are hidden and row painting is skipped.
The view uses minimal viewport updates, background caching, and Qt painter-state optimizations.

The committed [`phase-7-large-graph.json`](phase-7-large-graph.json) reports the canonical local
offscreen 500-node run:

| Measurement | Result | Gate |
| --- | ---: | ---: |
| Scene construction | 33.708 ms | ≤ 5000 ms |
| One parameter edit and scene update | 8.900 ms | ≤ 1000 ms |
| Initial embedded editors | 0 | exactly 0 |
| Editors after selecting one Number node | 3 | greater than 0 |
| Visible low-detail ports/editors | 0 | exactly 0 |
| Unaffected item identity retained | yes | required |

This measures graph-authoring UI work only. It is not a decoder, Scheduler, engine-process, GPU, or
hardware throughput benchmark.

## Tests and final acceptance

Final acceptance ran on Windows with CPython 3.12:

| Command | Result |
| --- | --- |
| `uv run pytest -q tests/phase7` | Passed; 36 Phase 7 tests across all nine ordered batches. |
| `uv run pytest -q` | Passed; 742 tests including every prior phase and smoke regression. |
| `uv run check` | Passed; 195 Python files formatted, Ruff clean, strict Pyright 0 errors/warnings/information. |
| `$env:QT_QPA_PLATFORM='offscreen'; uv run synmachine --smoke-test` | Passed; production UI started and stopped normally. |
| `uv run python -m tools.phase7_recovery ...` | Passed; forced exit 73 restored the unsaved node and original path. |
| `uv run python -m tools.phase7_validate_graphs examples tests/fixtures/phase7` | Passed; 10 valid, 0 invalid, 2 migrated. |
| `uv run python -m tools.phase7_large_graph` | Passed all five 500-node responsiveness/detail criteria. |
| `git diff --check` | Passed before the final documentation checkpoint. |

Coverage includes exact undo/redo for all new command types; alignment/distribution/tidy invariants;
settings round-trip/fallback; recent-file cleanup; atomic backup/failure cleanup; recovery ordering and
corrupt-manifest fallback; a real forced-exit subprocess; relative-tree relocation; exact and mismatch
media identity; pure single/multi-step migrations; corrupt/unknown graph errors; navigable diagnostics;
single-source transport refusal rules; incremental item identity; lazy editors; and low-detail culling.

## Manual UX checks

- The real application composition passed an offscreen start/stop smoke check with all new docks,
  menus, actions, timers, engine client, and settings ownership constructed.
- UI-focused tests exercised group/comment creation and editing paths, layout actions, preferences,
  missing-media action wiring, validation navigation signals, Inspector help rendering, transport
  enablement/targeting, selection-preserving incremental scene updates, and lazy-editor materialization.
- The 500-node profile instantiated the real `GraphScene` and `GraphView`, processed Qt events, edited
  and selected nodes, and entered low-detail mode.

No claim is made that a person visually inspected every widget on a physical Windows display during
this checkpoint; the manual-equivalent checks above were deterministic offscreen interactions.

## Limitations

- Recovery proves abrupt process termination after a completed durable autosave. It does not emulate
  power loss or storage-device failure during an individual filesystem flush/replace.
- The sampled media fingerprint reads at most the first and last 1 MiB plus file size. It is intended
  for relocation identity and mismatch warnings, not adversarial cryptographic provenance.
- Missing-media relink is explicit one item at a time; recursive library search/batch relink is not
  included.
- Lazy embedded editors begin at 200 nodes. Selecting a node materializes its editors for the remaining
  scene lifetime; this is bounded by user interaction rather than an eviction cache.
- Large-graph timings are one offscreen run on the recorded machine and are not universal display/GPU
  guarantees. Runtime graph performance remains covered by the earlier phase tools.
- Only known historical Number and Load Video v0 node shapes have built-in migrations. Unknown old
  implementation versions fail with `node_migration_failed` rather than guessing.

## Deviations

No architecture deviation, dependency addition, graph-schema bump, or ADR was required. Two scoped
implementation choices are documented rather than treated as hidden behavior: media identity uses a
bounded sampled SHA-256 rather than hashing arbitrarily large videos in full on every save, and the
large-graph acceptance tool uses offscreen Qt interaction instead of a physical-display frame-rate
measurement.

## Next work

Phase 7 is complete. Phase 8 is the next eligible phase and was intentionally not started.
