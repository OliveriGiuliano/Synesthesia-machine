# Persistence and model-field playbook

## Changing persisted graph data

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

## Adding or changing model fields

- Search for every explicit construction and reconstruction of the changed dataclass. Audit graph
  persistence, clipboard serialization, copy/paste, duplicate, remapping, undo/redo, runtime payloads,
  fingerprints, view models, and tests even when only one path appears user-visible.
- A presentation field on `NodeModel` or `ConnectionModel` must round-trip through graph save/load and
  all clipboard/duplication paths unless an explicit design decision says otherwise.
- If a clipboard JSON shape changes, increment `CLIPBOARD_FRAGMENT_VERSION`, continue reading every
  supported legacy version, normalize parsed fragments to the current version, and add legacy plus
  current round-trip tests.
- Prefer `dataclasses.replace()` for identity-preserving clones and remaps. If formats need different
  schemas, centralize shared field conversion so a future field cannot be silently dropped.
- Tests must assert semantic field values after every transformation, not merely object counts or
  structural validity.
