# 02: Hardened architecture boundary tests

**What to build:** The guard is a 45-line substring scan over a hardcoded 5-package tuple plus a top-level-only AST walk of contracts: a new headless package is unchecked until someone edits the tuple, in-function lazy imports are invisible, and the AGENTS.md rules for media, midi, and diagnostics (no UI/app imports) are never enforced.

**Solution:** One table of package -> forbidden import prefixes evaluated over every import node via ast.walk; derive the headless set from the package directory so a new package is enforced by default (explicit allowlist instead of hardcoded tuple).

- [x] Enforcement defaults to on
- [x] One table is the true boundary
- [x] Media/midi/diagnostics gap closed
- [x] Lazy imports can no longer hide
- [x] Targeted tests pass; `uv run check` green; no unrelated diff

**Blocked by:** 01

**Status:** resolved

**Files:**
- `tests/architecture/test_boundaries.py`

