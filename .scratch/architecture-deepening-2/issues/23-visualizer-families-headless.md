# 23: Give visualizer families a headless home

**What to build:** The invariants 'which family a display visualizer belongs to' and 'one display visualizer per family' (adding a Display Image Data silently removes the existing one) are invented by the UI — session.py plus AddNodeCommand — and re-declared by the demand-root policy; grep shows the graph package knows nothing about visualizers, so the validator happily accepts multiple display visualizers while the editor silently replaces them.

**Solution:** Declare visualizer family as node-definition metadata (a headless policy the session, the add-node command, and compute_demand_roots all consult). Adding a validation issue for multiple display visualizers would change ValidationReport semantics and needs its own ADR — out of scope for this ticket.

**ADR:** Metadata-only move is safe; if multiple-visualizer validation is ever wanted it is a graph-semantics change requiring a new ADR (flagged, not included).

**Blocked by:** 21

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/visualization/core.py`
- `src/synesthesia_machine/ui/session.py`
- `src/synesthesia_machine/ui/commands/graph_commands.py`
- `src/synesthesia_machine/ui/demand_roots.py`

**Acceptance:**
- [x] Domain invariant has a headless owner
- [x] UI becomes consumer, not inventor
- [x] Testable in tests/graph and tests/nodes
- [x] Editor and validator stop disagreeing silently
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
