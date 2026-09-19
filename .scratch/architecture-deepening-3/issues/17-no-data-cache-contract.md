# 17: Name the NoData and cache contract in the scaffold

**What to build:** The `NodeRuntime` protocol a node author implements (`process`/`reset`/`close`) never mentions `NoData`, yet the load-bearing rule — "your `process` is never invoked with `NoData` inputs unless you set `handles_no_data=True`" — lives only in `scheduler.execute_tick` (`scheduler.py:171-177`), so a domain that wants NoData-aware runtimes must read the scheduler to learn the rules. Symmetrically, a runtime that declares `handles_no_data=True` and forgets a declared output port gets `NoData` filled silently by the scheduler (`values[output_key] = outputs.get(port_id, NoData)`), a contract violation indistinguishable from an intentional `NoData` and pinned by no test. `CachePolicy` presents three values but `AUTO` and `STATIC` are behaviourally identical at execution — the compiler's only check is `cache_policy is not CachePolicy.NEVER`, and `STATIC`'s sole visible effect is suppressing auto-connectable parameters — so the policy the author declares is either a lie (two names, one behaviour) or an unexplained distinction, and the short-circuit is pinned by exactly one test plus the per-node conformance helper that routes through the full scheduler.

**Solution:** Name the contract where the author declares it: the scaffold states the `NoData` rule (inputs, and the missing-output behaviour) at `handles_no_data` and in the `NodeRuntime` protocol docs; settle the `AUTO`/`STATIC` question deliberately — pin their execution equivalence with a test and document it, or make `STATIC` earn its distinction, or retire it; add the missing-output-port behaviour as a pinned contract test.

**Status:** needs-triage

**Blocked by:** 15

**Files:**
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/runtime/scheduler.py`
- `tests/runtime/test_scheduler.py`
- `tests/support/image_conformance.py`

**Acceptance:**
- [ ] The NoData input rule and the missing-output behaviour are stated in the scaffold, at the point the author declares `handles_no_data`
- [ ] The AUTO/STATIC decision is recorded: equivalence pinned + documented, distinction implemented, or the value retired
- [ ] A missing declared output port is pinned by a test as deliberate contract, not accident
- [ ] The short-circuit keeps its test surface (one test is not enough: add the `handles_no_data=True` invocation case)
- [ ] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (node-scaffolding scout F4). The AUTO/STATIC call is a behaviour decision — decide in design before touching `base.py`.
