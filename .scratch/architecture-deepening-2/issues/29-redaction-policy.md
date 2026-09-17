# 29: Redaction as a registered policy

**What to build:** create_diagnostic_bundle is already a deep interface, but inside it three redaction strategies are selected ad-hoc per artifact (key/absolute-value redaction for hardware and graph, regex token redaction for metrics and profiles, line-truncation redaction for logs); the README promises one policy, a new artifact kind requires its author to know which strategy fits, and redact_sensitive_paths is exported so a caller could apply the wrong one themselves.

**Solution:** Keep the public signature; factor the sanitizers into a declared per-artifact-kind strategy table inside the module (every artifact kind must name its strategy), and drop the exported raw sanitizer from the facade or document it as an escape hatch for arbitrary JSON.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/diagnostics/bundle.py`
- `tests/diagnostics/test_bundles.py`

**Acceptance:**
- [x] Privacy knowledge in one place
- [x] New artifact must declare its strategy
- [x] Strategy table exhaustively testable
- [x] README promise becomes true
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
