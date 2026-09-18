# 16: Registration-time definition validation

**What to build:** The only check a node definition receives when it enters the system is `NodeRegistry.register`'s duplicate-`type_id` check. A definition whose `implementation_version` is 3 but whose `migrations` map covers only version 1 — or whose chain jumps versions — is accepted at registration and fails only when a user loads an old graph, via `migrate_node_data` called from graph load (`persistence/graph_io.py:465`), which raises per step ("must produce version N+1", "No node migration is registered"). The invariant the whole persistence stack depends on — "a registered node is loadable and migratable" — is not enforced at the one place a definition is admitted; the walk logic already exists in `nodes/migrations.py`, it just runs at the wrong time. No test constructs a deliberately incoherent definition and asserts the registry rejects it.

**Solution:** Make registration the deep operation: `NodeRegistry.register` (or a `validate()` it calls on the definition) performs the coherence checks the model already knows — the metadata invariants from `NodeDefinition.__post_init__`, plus migration-chain contiguity from version 1 to `implementation_version` for nodes that declare migrations, plus a runtime-factory/declared-ports check where feasible — so an incoherent definition is rejected where it is admitted, and the load-time walk remains as a backstop for hand-edited files.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/registry.py`
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/nodes/migrations.py`
- `tests/architecture/` (admission validation)
- `tests/persistence/test_migrations.py` (backstop still covered)

**Acceptance:**
- [x] A version-3 definition with migrations covering only version 1 is rejected at registration (new test)
- [x] A chain with a version gap is rejected at registration
- [x] The built-in registry passes admission validation for all 50 definitions
- [x] Load-time migration checks remain as a backstop and their tests still pass
- [x] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (node-scaffolding scout F5). Deletion test: the registry's admission check is the only enforcement point of "registry contents are loadable" — concentrating the walk here moves no complexity anywhere else.
- 2026-09-18: Implemented. The coherence check lives in `NodeDefinition.__post_init__` (`_check_migration_chain`) rather than in `NodeRegistry.register`: construction is the upstream choke point every definition passes through - `register`, `dataclasses.replace`, and direct construction all re-run `__init__`, so an incoherent definition cannot exist anywhere in the process, and the registry's duplicate-ID check keeps owning ID uniqueness. Invariant: migration keys must form a contiguous run `[lowest, implementation_version - 1]` with `lowest >= 0` - saved payloads start at version 0 (the video node's legacy `{0, 1}` chain at v2 is the pattern), so strict `{1, ..., v-1}` would have rejected every migrated node. The deliberate runtime-factory smoke check was skipped: factories bind device services (MIDI ports, camera), so invoking them while building the registry would add device side effects to the headless `create_builtin_registry()` path; declared ports are validated by the compiler and the scheduler drives each factory per instance at plan time. Tests: `tests/contracts/test_values.py` (gap, unreachable key, negative key, contiguous-run acceptance, built-in catalogue admission) - the file already hosts `NodeDefinition` invariant tests. Load-time backstop (`migrate_node_data`) and `tests/persistence/test_migrations.py` untouched and green. Verification: `uv run check` green; full suite 1373 passed / 3 skipped (one pre-existing flake in `test_process_previews.py` forced-termination slot tests reproduces on the pristine tree).
