# 18: Move per-node migrations next to their definitions

**What to build:** The migrate_node_data walker is deep and central (correct), but roughly ten per-node migration functions live in the same shared file — each referenced by exactly one NodeDefinition — so understanding a node's v1-to-v2 payload change requires a jump from the node file to migrations.py and back through the definition's migrations map.

**Solution:** Keep the walker and its types in the central module (or base) but move each per-node step function into the module that owns the definition; definitions already hold callables, so no new dependency appears and the persistence call site is unchanged.

**Status:** open

**Files:**
- `src/synesthesia_machine/nodes/migrations.py`
- `src/synesthesia_machine/nodes/utility/core.py`
- `src/synesthesia_machine/nodes/image/*.py (definition files with migration maps)`

**Acceptance:**
- [ ] Behaviour change and its migration sit together
- [ ] Churn leaves the shared dumping ground
- [ ] Walker stays the single deep driver
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
