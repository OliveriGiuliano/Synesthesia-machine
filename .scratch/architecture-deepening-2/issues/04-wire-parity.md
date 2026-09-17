# 04: Concentrate the wire-parity mapping

**What to build:** The graph exists in three representations — domain models, WireNode/WireConnection, and the converter pair — and a NodeModel field change must be mirrored by hand in WireNode and in both converter directions, exactly the manual repetition the cross-cutting change audit warns about.

**Solution:** Co-locate the mapping next to the models it maps (snapshot.to_payload() / GraphSnapshot.from_payload(), or one dedicated parity module) and add a field-parity test asserting the wire types track the model fields, so drift is a red test instead of a silent gap across the process seam.

- [x] Model field change = one-file edit
- [x] Parity proven by test, not memory
- [x] Wire types stay in contracts (ADR-0005 untouched)
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

**Status:** resolved

**Files:**
- `src/synesthesia_machine/graph/model.py`
- `src/synesthesia_machine/contracts/__init__.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/runtime/graph_payload.py` (deleted)
- `tests/graph/test_wire_parity.py`
- `tests/runtime/test_engine_client.py`
- `tests/runtime/test_process_engine.py`
