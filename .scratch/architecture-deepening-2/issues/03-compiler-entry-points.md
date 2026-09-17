# 03: Compiler entry points

**What to build:** There is no validate-only entry point: the sole producer of ValidationReport is GraphCompiler.compile(), which always runs plan construction, so every 'is this graph valid?' caller (random_graph preconditions, connection-compatibility queries that double-compile) pays for a full ExecutionPlan. Separately, CompilationResult.resolved_types leaks a raw (node, port, is_output-bool) key convention that ui/view_models.py re-implements with literal booleans — a wrong bool degrades silently to a fallback type name.

**Solution:** Expose GraphCompiler.validate() -> ValidationReport running the same deterministic stage pipeline without plan construction; add a named resolved_type(node_id, port_id, is_output) accessor so the tuple convention dies in both files; move the pure layout-algorithm tests from tests/ui to tests/graph where the testing playbook puts them.

- [x] Validation is a named testable concept
- [x] Compatibility checks compare reports, not discarded plans
- [x] Wrong port key becomes an error, not a fallback
- [x] Tests sit where the playbook says
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

**Status:** resolved

**Files:**
- `src/synesthesia_machine/graph/compiler.py`
- `src/synesthesia_machine/graph/__init__.py`
- `src/synesthesia_machine/graph/random_graph.py`
- `src/synesthesia_machine/ui/view_models.py`
- `tests/ui/test_layout_commands.py -> tests/graph/`
