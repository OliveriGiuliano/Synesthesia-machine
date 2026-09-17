# 18: Move per-node migrations next to their definitions

**What to build:** The migrate_node_data walker is deep and central (correct), but roughly ten per-node migration functions live in the same shared file — each referenced by exactly one NodeDefinition — so understanding a node's v1-to-v2 payload change requires a jump from the node file to migrations.py and back through the definition's migrations map.

**Solution:** Keep the walker and its types in the central module (or base) but move each per-node step function into the module that owns the definition; definitions already hold callables, so no new dependency appears and the persistence call site is unchanged.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/migrations.py`
- `src/synesthesia_machine/nodes/utility/core.py`
- `src/synesthesia_machine/nodes/image/*.py (definition files with migration maps)`

**Acceptance:**
- [ ] Behaviour change and its migration sit together
- [ ] Churn leaves the shared dumping ground

# Answer

Shipped: `nodes/migrations.py` is now 95 lines — the `migrate_node_data` walker, its value types, and the one shared payload helper (`_parameters` promoted to public `migration_parameters` since step functions in five subpackages need it). Every per-node step moved next to its definition: number (utility/core), load_video v0→v1/v1→v2 (input/video), statistics (utility/dynamic), display_image_data + channel_display (visualization/core), hue/invert_colour/clamp (image/adjustments), separate/combine channels (image/channels), change_colour_space (image/utilities). The three image-shared rewrites (`rewrite_colour_space_target`, `rewrite_channel_selection`) and the cross-family `migrate_adjustment_channel_selection_v1_to_v2` live in `image/runtime_support.py`, shared by adjustments and filters. test_migrations imports the steps from their new owners; behaviour is unchanged (full suite green).
- [ ] Walker stays the single deep driver
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
