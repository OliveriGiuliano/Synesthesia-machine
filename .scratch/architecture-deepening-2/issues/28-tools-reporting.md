# 28: Shared evidence-report infrastructure for tools

**What to build:** Every evidence tool re-declares the same report envelope (generated_at_utc / git_commit / environment / methodology / passed) and its own JSON writer and git-state probe; and two overlapping environment summaries exist — diagnostics/hardware.py (psutil + NVML) and tools/environment_report.py — with subtly different field sets, embedded by different tools with no declared relationship.

**Solution:** A small tools/reporting.py (EvidenceReport envelope, write_report, git_state()) that tools import instead of re-rolling; derive the tool-oriented environment summary from diagnostics.hardware so 'environment' has one owner with the tool projection on top.

**Status:** resolved

**Files:**
- `tools/reporting.py (new)`
- `tools/environment_report.py`
- `src/synesthesia_machine/diagnostics/hardware.py`
- `tools/hold_image_soak.py`
- `tools/synesthesia_benchmarks.py`
- `tools/reference_benchmark.py`
- `tools/runtime_soak.py`
- `tools/profiler_overhead.py`
- `tools/large_graph_profile.py`

**Acceptance:**
- [x] Evidence conventions in one module
- [x] Environment summary stops drifting
- [x] Subprocess git shims deleted
- [x] New tool = one import
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
