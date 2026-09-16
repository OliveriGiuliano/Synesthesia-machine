# 12: Retire BUILTIN_NODE_MIGRATIONS from graph_io

**What to build:** Persistence migrates saved nodes solely through their definitions; the separately hard-coded migration table is removed, so the migration mechanism is a single injectable path symmetric with the node registry. A node version bump becomes a definition-only change.

**Blocked by:** 06 (Own the migration chain in NodeDefinition)

**Status:** done — `BUILTIN_NODE_MIGRATIONS` + `node_migrations.py` removed; `graph_io` already routed through the definition chain (`nodes.migrations.migrate_node_data` + `NodeDefinition.migrations`); injectable via the `NodeRegistry` param; persistence suite (79) + example/fixture loading (20) + grep + gates green

- [x] `BUILTIN_NODE_MIGRATIONS` is removed from `graph_io`; all node migrations run through the definition.
- [x] The migration path is injectable/testable like the node registry (a test can supply a custom migration).
- [x] All saved-graph, clipboard, and example fixtures still load and migrate correctly.
