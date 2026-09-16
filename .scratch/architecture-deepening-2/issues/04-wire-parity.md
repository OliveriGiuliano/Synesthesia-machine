# 04: Concentrate the wire-parity mapping

**What to build:** The graph exists in three representations — domain models, WireNode/WireConnection, and the converter pair — and a NodeModel field change must be mirrored by hand in WireNode and in both converter directions, exactly the manual repetition the cross-cutting change audit warns about.

**Solution:** Co-locate the mapping next to the models it maps (snapshot.to_payload() / GraphSnapshot.from_payload(), or one dedicated parity module) and add a field-parity test asserting the wire types track the model fields, so drift is a red test instead of a silent gap across the process seam.

**Status:** open

**Files:**
- `src/synesthesia_machine/runtime/graph_payload.py`
- `src/synesthesia_machine/contracts/engine_messages.py`
- `src/synesthesia_machine/graph/model.py`
- `tests/runtime/`

**Acceptance:**
- [ ] Model field change = one-file edit
- [ ] Parity proven by test, not memory
- [ ] Wire types stay in contracts (ADR-0005 untouched)
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
