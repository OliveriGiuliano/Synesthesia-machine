# 11: Roll out single-coercion across node families

**What to build:** Every node family reads its parameters as validated typed values from the parameter spec and its per-consumer coercion/type-guard helpers are deleted, so a parameter is coerced exactly once at the `process()` seam. This is a wide, batched-by-node-family refactor; run it only if the pilot (05) validated the payoff.

**Blocked by:** 05 (Coerce-once pilot on one node family)

**Status:** done - all node families (synesthesia, image, utility, output) read parameters as validated typed values via `cast`/direct reads; all 8 `runtime_support` coercion helpers retired (module is now just `StatelessImageRuntime`); per-consumer guards deleted; legitimate TypeVariable narrowing kept; 386 node tests + 21 integration tests + ruff/pyright clean, acceptance grep zero matches

- [x] All node families read validated typed parameter values in `process()`; the duplicated per-consumer coercion/type-guard helpers are removed.
- [x] One coercion path remains, owned by the parameter spec (the scheduler seam); the `runtime_support` coercion helpers are retired.
- [x] Node behaviour is unchanged for valid, boundary, and NaN/infinity inputs across the suite.
- [x] Rolled out in batches sized by blast radius (synesthesia → image → utility → output), keeping tests green batch to batch.
