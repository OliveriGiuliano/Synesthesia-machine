# 30: Public check registry for the packaged self-test

**What to build:** run_packaged_smoke composes its seven checks as a hardcoded list with a positional checks.insert(1, ...) for the h264 slot, and the suite couples to private names: tests monkeypatch seven private check functions and call release_smoke._graph_round_trip directly, so renaming any check breaks the suite with no type error.

**Solution:** Represent the checks as an ordered registry of named, public check factories; run_packaged_smoke keeps its exact interface and the h264 check becomes a normal conditional entry; tests target the registry by name.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/app/release_smoke.py`
- `tests/packaging/test_release.py`

**Acceptance:**
- [x] Public seam instead of private-name coupling
- [x] The h264 slot is a declared conditional
- [x] Renames caught by the type checker
- [x] Report ordering lives with the checks
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
