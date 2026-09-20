# 15: Split the node record along its consumers

**What to build:** `NodeDefinition` is a 25-field record whose interface is the union of four consumers' needs — the compiler reads port/type/required/variadic/execution/cache, the scheduler reads runtime/no-data/validator/execution, persistence reads version + migrations + media parameter, and the UI reads editor hints, preview dock, parameter groups, and a `ParameterEditorResolver` typed to receive live `SourceStatus` (a headless record reaching into engine device status, contradicting the nodes README's dependency claim). An image transform uses ~14 of the 25 fields, a source a different subset, a scalar node fewer still — and `realtime_safe` (`base.py:512`) has zero consumers anywhere in `src/` or `tests/`. The authored `ParameterSpec` is additionally rewritten twice before execution (frozen-dataclass `__post_init__` auto-fill of `connected_port_type`, then `_resolve_parameter_socket` silently dropping `connectable` for source/visualizer nodes from the execution kind), and three value methods (`validate`, `sanitize_value`, `connected_value`) partition one value contract that must stay mutually consistent by hand — no test asserts the triangle, and the INT step-origin arithmetic is pinned for positive out-of-bounds inputs only.

**Solution:** Split the record along the consumers' projections — an execution contract (compiler/scheduler), a persistence descriptor (migrations, media references), and a presentation intent (UI, including the status-fed resolver) — so each consumer imports only what it reads and a node author faces the record their node kind uses; zero-consumer fields are deleted; the three value methods collapse into one ladder (what it produces, `validate` accepts — asserted once). This is a design-it-twice candidate: explore at least two record shapes before settling.

**Status:** resolved

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
- 2026-09-20: Design record (design-it-twice over the full field-consumer audit, research/15). Shapes explored:
  (1) disjoint triptych (independent execution/persistence/presentation records) — rejected:
  `implementation_version`, `execution_kind`, `parameters`, `inputs/outputs` are each read by
  three or four consumers; disjoint records would duplicate them or force cross-record reads.
  (2) per-kind records (SourceNodeDefinition/…) — rejected: the kind is already a field
  (`ExecutionKind`); a type hierarchy would force registry/compiler branches on concrete types.
  (3) CHOSEN — shared execution core + named satellites, aggregated by the author-facing
  `NodeDefinition`: `NodeExecutionContract` (type_id, implementation_version, execution_kind,
  inputs, outputs, resolved parameters, variadic_input, runtime_factory, cache_policy,
  handles_no_data, port_type_resolver, required_input_resolver, parameter_validator,
  source_outputs, source_config_builder — carries the query methods input()/input_ports()/
  output()/parameter()/parameter_values()/port_type()/required_inputs()), `NodePersistenceDescriptor`
  (migrations, media_parameter_id) and `NodePresentationIntent` (display_name, category,
  description, aliases, preview_dock, parameter_groups, parameter_editor_resolver).
  `NodeDefinition(execution, presentation, persistence=None)` is what authors write and what the
  registry stores; consumers read the sub-record they need; `CompiledNode.definition` narrows to
  the execution contract so no presentation/persistence material reaches the engine working set
  (both processes still build their own registries — definitions never cross IPC).
  Seam fix: `ParameterEditorResolver` takes a narrow `SourceEditorFacts(file_path, duration_s)`
  value projected from the engine's `SourceStatus` by the UI — node metadata stops naming engine
  device status (restores the nodes README's self-description). `realtime_safe` deleted.
  The two authored-spec rewrite passes collapse into one normalization pass on
  `NodeExecutionContract.__post_init__` (ParameterSpec.__post_init__ keeps pure validation only).
  The three value methods stay as an explicit ladder (connected_value → sanitize_value → validate)
  over one shared scalar-coercion function, with a new consistency test asserting the
  sanitize/validate agreement the ticket calls for.
- 2026-09-20: Implemented and committed. `NodeDefinition` is now the author-facing aggregate
  over `NodeExecutionContract` / `NodePersistenceDescriptor` / `NodePresentationIntent`;
  consumers read only their sub-record (compiler/scheduler/engine: execution; persistence:
  execution identity + persistence; editor: presentation + execution), `CompiledNode.definition`
  is the execution contract, `ParameterEditorResolver` takes the narrow `SourceEditorFacts`
  projected from `SourceStatus` by `ui/view_models.py`, `realtime_safe` is deleted, and the
  authored-spec rewrite runs in one `NodeExecutionContract.__post_init__` pass.
  Verification: `uv run check` green (ruff format/lint + strict pyright); full pytest suite
  green (1442 passed, 3 Windows-only skips). Two-axis review performed against the acceptance
  criteria and AGENTS.md standards (records frozen/slotted with neighbor-consistent
  `__post_init__` validation, facade `__all__` complete, no nodes/ dependency on engine
  status types). Limitations: the independent fresh-context review subagents were aborted by
  model-endpoint instability and the review was completed by the implementing agent; the
  process-preview reactivation slot-announcement test is load-flaky (3 s deadline in the
  spawned engine) — it passes in isolated and clean full-suite runs.
