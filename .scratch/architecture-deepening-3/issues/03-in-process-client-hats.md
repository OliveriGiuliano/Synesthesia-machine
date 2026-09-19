# 03: One hat per placement for the in-process client

**What to build:** `InProcessEngineClient` is two modules in one: the in-process UI adapter *and* the spawned child's engine body (`EngineServer.__init__` constructs it with a hardcoded `create_builtin_registry()`), so its effective interface is placement-dependent. `EngineServer._handle` never calls `status()`, `restart()`, `next_image_previews()`, `clear_previews()`, or the preview-cursor resets — no such commands exist in protocol 16 — so roughly six of the protocol's 26 methods are dead in the child placement, and the child's `_EventPublisher` drives the child client's note/value cursors directly (a hidden coupling admitted in a comment at `in_process_engine.py:735-740`: "the reset must live here, not in a wrapper"). `ProcessEngineClient` is a remote handle to the very class that is the production engine, inverting the adapter relationship at the most load-bearing seam in the cluster: any question about the production transport ("how does a seek land") forces reading `engine_client.py` *and* `in_process_engine.py` *and* `engine_server.py`, because the child's behaviour **is** `InProcessEngineClient`.

**Solution:** Separate the engine body (facade/scheduler/source orchestration — what the child runs) from the protocol client (what a driver talks to), so each file's liveness is placement-static: the in-process placement composes body + client, the child placement runs the body behind the wire, and the child-side cursor/publisher coupling becomes a named seam instead of an inlined comment. Both placements keep their current observable behaviour.

**Status:** needs-triage

**Blocked by:** 01

**Files:**
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/app/shell.py`

**Acceptance:**
- [ ] A reader of the in-process module meets one role per file; the placement-dead protocol surface is not part of the child-facing module
- [ ] The child-side preview publisher/cursor coupling is a named seam
- [ ] An ADR records the placement decision (engine-body placement is architectural)
- [ ] Both transports' integration tests (in-process and spawned) pass unchanged
- [ ] `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (report candidate "One driver, four engine modes"; engine-cluster scout F1). Largest structural change in this run — scope before starting.
