# Ticket 07 plan — Tighten the engine IPC seam

## Impact matrix (cross-cutting: contracts + runtime + tools + tests)

| Item | Owner | Consumers affected |
|---|---|---|
| `ENGINE_PROTOCOL_VERSION` 15→16 | contracts/engine_messages.py | client handshake, server mismatch check, probe `_validate_protocol`, tests using the constant (auto); one hardcoded literal in test_device_catalogue.py:175 |
| `WriteSharedFrame`/`SharedFrameReady` removal | contracts (dataclasses + unions + facade) | engine_server (import, exempt list, `_write_probe_frame`, `_handle` branch), tools/process_ipc_probe.py (→ local message classes), tests/smoke/test_process_ipc.py (→ import from tools), contracts `__init__` `__all__` |
| `RemoteErrorKind` vocabulary + `CommandFailed.error_type` typing | contracts (vocabulary), server (classification owner: `classify_remote_error`), client (raise owner: `_raise_remote_error`) | engine_server `_send_failure`, engine_client `_raise_remote_error`, tests asserting `error_type` (test_process_engine.py:391) |
| One heartbeat constant | contracts/engine_client.py `DEFAULT_HEARTBEAT_TIMEOUT_S` | runtime/engine_client.py (import, was local), runtime/engine_session.py (replaces `_HEARTBEAT_TIMEOUT_S` mirror) |
| EngineServer injection seam | runtime/engine_server.py `__init__(connection, event_queue, *, engine=None, events=None, heartbeat_interval_s=…)` | production wiring stays default; engine_server_main unchanged; tests build real servers (test_process_engine.py ×2, test_output.py ×1) |

Lifecycle: handshake/mismatch semantics unchanged; both sides of the seam bump in one atomic commit (spawned children share code) so no parent/child skew window. No persisted protocol state; graph schema/clipboard/examples untouched. ADR-0005 child purity is strengthened (probe code leaves the production child), not contradicted.

## Edits

1. contracts/engine_client.py: add `DEFAULT_HEARTBEAT_TIMEOUT_S = 2.0`
2. contracts/engine_messages.py: version 16; `RemoteErrorKind` StrEnum (key, not_implemented, value, stale_revision, other); `CommandFailed.error_type: RemoteErrorKind`; delete WriteSharedFrame/SharedFrameReady + union members
3. contracts/__init__.py: drop probe exports; export `DEFAULT_HEARTBEAT_TIMEOUT_S`, `RemoteErrorKind`
4. runtime/engine_client.py: import constant from contracts; `_raise_remote_error` maps kinds → KeyError/NotImplementedError/ValueError/RuntimeError
5. runtime/engine_session.py: import constant from contracts; delete mirror literal
6. runtime/engine_server.py: drop probe imports + numpy + SharedMemory import; exempt list −WriteSharedFrame; delete `_write_probe_frame` + branch; `classify_remote_error`; constructor injection
7. tools/process_ipc_probe.py: local WriteSharedFrame/SharedFrameReady dataclasses (spawn-importable, top-level)
8. tests/smoke/test_process_ipc.py: import probe classes from tools
9. tests/runtime/test_device_catalogue.py: `== 16`
10. tests/runtime/test_process_engine.py: real EngineServer constructions; stale assertion → `RemoteErrorKind.STALE_REVISION`
11. tests/midi/test_output.py: real EngineServer construction

## Verification

- `uv run check`
- targeted: tests/smoke/test_process_ipc.py, tests/runtime/test_process_engine.py, tests/runtime/test_device_catalogue.py, tests/midi/test_output.py
- probe tool run: `uv run python -m tools.process_ipc_probe` (per tools/README)
- full `uv run pytest -q` + offscreen `--smoke-test`
