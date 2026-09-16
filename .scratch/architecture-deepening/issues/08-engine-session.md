# 08: Add an EngineSession facade owning client-side bookkeeping

**What to build:** Driving the engine from a caller no longer requires knowing protocol detail — sequence cursors, the connection-state machine, revision staleness, and last-valid restart semantics live inside a small EngineSession facade over the client protocol. A caller polls `next_*_previews()` with no cursor and reads a coherent connection state; a new caller (a headless tool or a test) can drive the engine by learning one small interface.

**Blocked by:** 04 (Single-source the engine message sets)

**Status:** done — `runtime/engine_session.py` `EngineSession` facade (cursor-free `next_*_previews()`, 6-state connection machine, last-valid restart via `RestartOutcome`) + 23 headless tests; exported from the runtime facade; ruff/Pyright/tests green

- [x] An EngineSession facade owns sequence-cursor, graph-revision, and connection-state bookkeeping across both adapters.
- [x] Callers poll `next_*_previews()` with no `after_sequences` argument; the 6-state connection machine, heartbeat staleness, and pause-panics-MIDI / last-valid restart semantics are hidden behind the session.
- [x] The in-process and child-process paths behave identically through the session.
- [x] A headless test drives activate/transport/poll through the session without tracking cursors.
