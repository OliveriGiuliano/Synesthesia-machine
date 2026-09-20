# 17: Name the NoData and cache contract in the scaffold

**What to build:** The `NodeRuntime` protocol a node author implements (`process`/`reset`/`close`) never mentions `NoData`, yet the load-bearing rule — "your `process` is never invoked with `NoData` inputs unless you set `handles_no_data=True`" — lives only in `scheduler.execute_tick` (`scheduler.py:171-177`), so a domain that wants NoData-aware runtimes must read the scheduler to learn the rules. Symmetrically, a runtime that declares `handles_no_data=True` and forgets a declared output port gets `NoData` filled silently by the scheduler (`values[output_key] = outputs.get(port_id, NoData)`), a contract violation indistinguishable from an intentional `NoData` and pinned by no test. `CachePolicy` presents three values but `AUTO` and `STATIC` are behaviourally identical at execution — the compiler's only check is `cache_policy is not CachePolicy.NEVER`, and `STATIC`'s sole visible effect is suppressing auto-connectable parameters — so the policy the author declares is either a lie (two names, one behaviour) or an unexplained distinction, and the short-circuit is pinned by exactly one test plus the per-node conformance helper that routes through the full scheduler.

**Solution:** Name the contract where the author declares it: the scaffold states the `NoData` rule (inputs, and the missing-output behaviour) at `handles_no_data` and in the `NodeRuntime` protocol docs; settle the `AUTO`/`STATIC` question deliberately — pin their execution equivalence with a test and document it, or make `STATIC` earn its distinction, or retire it; add the missing-output-port behaviour as a pinned contract test.

**Status:** resolved

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
- 2026-09-20: Design record. Decisions:
  1. NoData rule named in the scaffold: a `NodeRuntime` protocol docstring states
     the scheduler's input rule (no `NoData` inputs reach `process` unless the
     execution contract declares `handles_no_data=True`) and the output rule (an
     invoked `process` must publish every declared output port; a missing port is
     filled with `NoData` by the scheduler, indistinguishable from an intentional
     `NoData`), plus a field comment at `NodeExecutionContract.handles_no_data`.
  2. AUTO/STATIC: keep `STATIC` and pin its one real distinction. At execution the
     two are equivalent (the compiler only excludes `NEVER`), and retiring
     `STATIC` would lose the parameter guard its sole user
     (`synmachine.utility.number`) relies on: driving a statically cached node
     with cable values contradicts the "fixed value" assertion. So `CachePolicy`
     gets a docstring documenting AUTO/NEVER/STATIC exactly that way, and two
     tests pin it: (a) execution equivalence — identical AUTO and STATIC
     definitions compile to the same `is_static` and neither re-executes on
     tick 2; (b) the distinction — a LIVE scalar parameter on a STATIC node
     normalizes to non-connectable while the AUTO twin stays connectable
     (explicit `connectable=True` still opts in).
  3. Missing-output-port behaviour pinned by a dedicated `handles_no_data=True`
     test: an invoked runtime that omits a declared output port gets `NoData`
     published for it — deliberate contract, not accident.
  4. The short-circuit test surface already contains both required cases
     (`test_fan_out_..._propagates_no_data` for the default,
     `test_node_that_handles_no_data_is_invoked` for the opt-in); both are kept.
- 2026-09-20: Implemented and committed per the design record. `NodeRuntime` and
  `CachePolicy` carry the contract docstrings, `handles_no_data` carries its field
  comment, and `tests/runtime/test_scheduler.py` gains the three pinned tests
  (missing-output fill, AUTO/STATIC execution equivalence, STATIC parameter
  distinction) alongside the two pre-existing short-circuit cases, which are kept.
  The commit also repairs ruff-format drift that the ticket-15 sweep left in
  `tests/persistence/test_graph_persistence.py` and `tests/ui/test_dynamic_ports.py`
  (whitespace-only; `uv run check` was failing on them).
  Verification: `uv run check` green (ruff format/lint + strict pyright); full
  pytest suite green (1445 passed, 3 Windows-only skips). Review: two-axis review
  completed by the implementing agent (standards: docstring/test conventions,
  code-accurate claims; spec: all five acceptance criteria verified against the
  diff) because the independent-review subagent endpoint kept stalling.
