# Node, graph, compiler, and scheduler playbook

## Adding or changing a node

1. Inspect `nodes/base.py`, a neighboring implementation, its package `__init__.py`, and the relevant
   conformance tests before coding.
2. Put reusable image/math behavior in `media/` or another headless helper when it is not inherently
   node-specific. Keep the node runtime as orchestration and contract enforcement.
3. Define immutable metadata with a stable namespaced `type_id`, stable input/output/parameter IDs,
   an `implementation_version`, explicit execution/cache behavior, validated defaults, and a runtime
   factory. Search the complete registry before selecting an ID.
4. Register the definition through the relevant `create_*_definitions()` or utility registry factory.
   `nodes/composition.py` is the headless built-in composition point; `app/registry.py` is only its
   application-facing compatibility name.
5. Test defaults and invalid parameters, nominal behavior, `NoData`, non-finite values, metadata and
   immutability, dynamic type/clock resolution, state reset/close, compilation, and persistence as
   applicable. Image/channel nodes should reuse the Phase 5 conformance helpers where suitable.
6. If persisted behavior changes, preserve IDs. Bump `implementation_version` and add a pure,
   sequential node migration when an old payload needs transformation. Do not put compatibility
   branches into the runtime.
7. Update the appropriate node/reference documentation and an example or catalogue acceptance
   artifact. The current disconnected catalogue is generated deterministically with:

   ```powershell
   uv run python -m tools.generate_catalogue
   ```

   This command overwrites `examples/phase6/catalogue.synmachine.json`. Run it only when the built-in
   registry intentionally changes. Do not rewrite a historical phase catalogue unless the task
   explicitly calls for it.

## Changing graph models, compilation, or scheduling

- Keep authoring models Qt-free and snapshots immutable. Mutations belong on `GraphDocument` and must
  advance its revision exactly when state changes.
- Validation should produce stable, navigable issue codes rather than UI dialogs or generic strings.
- Compilation must be deterministic: stable ordering, type resolution, cycle rejection, clock-domain
  analysis, demand reachability, and execution-plan construction should not depend on dict/set order.
- Preserve atomic live-edit behavior. Invalid graph revisions remain editable in the UI but must not
  replace the last valid active runtime.
- When changing state-retention keys or plan replacement, test both reusable and invalidated runtime
  paths, including factory/reset/close failures.
- Add focused Phase 1 tests and relevant in-process/process integration coverage. Scheduler changes
  often also require Phase 3, Phase 4, Phase 5 conformance, and Phase 8 profiling checks.
