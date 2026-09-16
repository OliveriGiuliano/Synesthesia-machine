# 01: Contracts layer hygiene

**What to build:** The lowest layer leaks across its own seam: contracts TYPE_CHECKING-imports graph (engine_client.py:21-23) because the wire value EngineActivation carries graph-layer ValidationReport; the boundary test only tolerates this by inspecting top-level statements. One persisted-literal concept is defined three times (graph LiteralValue, contracts SnapshotLiteral, ParameterValue), and the facade exports zero-caller helpers (is_no_data, freeze_metric_map).

**Solution:** Move ValidationIssue/ValidationReport/ValidationSeverity into contracts (graph re-exports them for facade stability), define the literal union once in contracts with graph and engine_messages referencing it, and delete the dead exports.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/contracts/runtime_values.py`
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/contracts/engine_messages.py`
- `src/synesthesia_machine/graph/validation.py`
- `src/synesthesia_machine/graph/__init__.py`

**Acceptance:**
- [x] Boundary test can scan every import
- [x] One literal definition, three references
- [x] Interface truthfulness: no dead exports
- [x] Report value sits next to its carrier
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
