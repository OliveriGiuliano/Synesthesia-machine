# Engine IPC and lifecycle playbook

- Inspect the public `EngineClient` protocol in `contracts/engine_client.py`, message dataclasses and
  unions in `contracts/engine_messages.py`, and both client/server implementations.
- If the wire contract changes, increment `ENGINE_PROTOCOL_VERSION` and update exports, message unions,
  handshake behavior, protocol mismatch handling, client, server, mocks, and spawn-process tests as one
  atomic change.
- Search documentation for duplicated protocol numbers. Prefer referring to
  `ENGINE_PROTOCOL_VERSION` without copying its numeric value; otherwise add an automated consistency
  check.
- Commands and responses should retain request IDs and graph revisions where relevant. Async events
  must be safe to ignore when stale.
- Test happy path, timeout, mismatch, child crash, restart, shutdown, shared-memory cleanup, stale
  revision, and double-close behavior as applicable.
- For plan or generation replacement, define which old data remains valid and which payloads, cursors,
  handles, and UI projections must be cleared. A rejected candidate preserves the working plan; a
  successful replacement must not expose data from the previous generation as current.
- Preserve best-effort MIDI note-off/panic and device cleanup on stop, reload, port change, graph
  replacement, engine failure, and application close.
