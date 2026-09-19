# 04: Split the supervisor record out of EngineMetrics

**What to build:** `EngineMetrics` mixes engine telemetry (state, processed ticks, fps, graph/node latency percentiles, mailbox metrics, runtime errors) with process-supervisor facts (`child_process_id`, `restart_count`, `heartbeat_age_s`, `uptime_s`, `cpu_percent`, `system_memory_bytes`), and the record is assembled in three layers of `dataclasses.replace` chains: the child-side client fills the engine fields, the child server's `QueryMetrics` handler replaces four supervisor fields, and the parent client replaces two more — or short-circuits the whole value into a synthesized `state=ERROR` record when the child is crashed/unresponsive. Over the in-process adapter the supervisor fields are permanently at defaults (`child_process_id=None`, `heartbeat_age_s=0.0`), which is indistinguishable from a process client whose child never started; downstream consumers (the session's known-stopped cache, `EngineBridge` telemetry, `MainWindow._apply_engine_telemetry`) inherit the ambiguity, and deleting any one layer's `replace` silently zeroes fields the UI renders.

**Solution:** A typed sub-record groups the supervisor facts so a consumer cannot treat `cpu_percent` as always meaningful; each layer fills only the record it owns; per-placement vacuity is documented on the type; the synthesized crashed-child metrics shape is pinned by a test. Whether the split crosses the wire as two fields, one nested field, or a protocol bump is a contract decision to settle first.

**Status:** needs-triage

**Blocked by:** 01

**Files:**
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/ui/engine_bridge.py`
- `tests/ui/test_process_supervision.py`

**Acceptance:**
- [ ] Supervisor facts are a separate typed record; engine telemetry and supervisor facts are filled by different owners
- [ ] `child_process_id=None` + `heartbeat_age_s=0.0` means one documented thing per placement, not two
- [ ] The synthesized ERROR metrics shape for a crashed child is pinned by a test
- [ ] Any wire-shape change is explicit (protocol bump or compatibility argument) and covered by the protocol-version consistency test
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (report candidate "One driver, four engine modes"; engine-cluster scout F5).
