# 07: Tighten the engine IPC seam

**What to build:** Message shapes are single-sourced, but behavioural knowledge is duplicated across the seam: the heartbeat timeout exists as two mirrored literals (engine_client.py DEFAULT_HEARTBEAT_TIMEOUT_S vs engine_session.py _HEARTBEAT_TIMEOUT_S, with a comment admitting the mirror), the server keeps a hand-maintained revision-exempt command list while the client scatters revision arguments per method, and error names are remapped one-sidedly. Test-only WriteSharedFrame/SharedFrameReady commands ride in every production child (cv2/numpy probe code in engine_server.py:506-519), and EngineServer has no injection seam, so tests build it via object.__new__ plus private-attribute surgery.

**Solution:** Advance ENGINE_PROTOCOL_VERSION 15 to 16 per the engine-ipc playbook: drop the test-only commands (the probe tool already carries its own mini server; the real-broker path is covered by test_process_previews.py), share one heartbeat-timeout constant across both sides, and give EngineServer an optional constructor injection for the engine and publisher with production wiring defaulting in engine_server_main.

**ADR:** Not a conflict — ADR-0005 child purity is strengthened; the bump follows docs/agent-playbooks/engine-ipc.md (full process test matrix).

**Blocked by:** 05, 06

**Status:** open

**Files:**
- `src/synesthesia_machine/contracts/engine_messages.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/engine_session.py`
- `tests/smoke/test_process_ipc.py`
- `tools/process_ipc_probe.py`

**Acceptance:**
- [ ] Production child stops carrying probe code
- [ ] One heartbeat constant, one error-remap owner
- [ ] Tests construct a real EngineServer
- [ ] New message types still flow via protocol unions
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
