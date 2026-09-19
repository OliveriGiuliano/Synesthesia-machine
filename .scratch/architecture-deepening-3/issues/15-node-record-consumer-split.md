# 15: Split the node record along its consumers

**What to build:** `NodeDefinition` is a 25-field record whose interface is the union of four consumers' needs — the compiler reads port/type/required/variadic/execution/cache, the scheduler reads runtime/no-data/validator/execution, persistence reads version + migrations + media parameter, and the UI reads editor hints, preview dock, parameter groups, and a `ParameterEditorResolver` typed to receive live `SourceStatus` (a headless record reaching into engine device status, contradicting the nodes README's dependency claim). An image transform uses ~14 of the 25 fields, a source a different subset, a scalar node fewer still — and `realtime_safe` (`base.py:512`) has zero consumers anywhere in `src/` or `tests/`. The authored `ParameterSpec` is additionally rewritten twice before execution (frozen-dataclass `__post_init__` auto-fill of `connected_port_type`, then `_resolve_parameter_socket` silently dropping `connectable` for source/visualizer nodes from the execution kind), and three value methods (`validate`, `sanitize_value`, `connected_value`) partition one value contract that must stay mutually consistent by hand — no test asserts the triangle, and the INT step-origin arithmetic is pinned for positive out-of-bounds inputs only.

**Solution:** Split the record along the consumers' projections — an execution contract (compiler/scheduler), a persistence descriptor (migrations, media references), and a presentation intent (UI, including the status-fed resolver) — so each consumer imports only what it reads and a node author faces the record their node kind uses; zero-consumer fields are deleted; the three value methods collapse into one ladder (what it produces, `validate` accepts — asserted once). This is a design-it-twice candidate: explore at least two record shapes before settling.

**Status:** needs-triage

**Blocked by:** 16

**Files:**
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/nodes/__init__.py` (facade)
- definition sites across `src/synesthesia_machine/nodes/{input,image,utility,output,synesthesia,visualization}/`
- `src/synesthesia_machine/graph/compiler.py`
- `src/synesthesia_machine/runtime/scheduler.py`
- `src/synesthesia_machine/persistence/graph_io.py`
- `src/synesthesia_machine/ui/view_models.py`

**Acceptance:**
- [ ] Each consumer imports only its record; no consumer reads a field its record does not declare
- [ ] A node author's definition names only the fields their node kind uses
- [ ] `realtime_safe` and any other dead residue are deleted
- [ ] One parameter-value ladder replaces the three methods, with a consistency test asserting sanitize/validate agreement
- [ ] `uv run check` green; full suite green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (node-scaffolding scout F1 + F3). The status-fed `ParameterEditorResolver` crossing into engine status is the sharpest seam leak in the record — settle in design whether presentation intent receives a narrower published value instead.
