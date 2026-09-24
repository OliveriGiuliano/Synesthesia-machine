# ADR 0031: Engine startup gets a dedicated handshake timeout

- Status: Accepted
- Date: 2026-09-24

## Context

`ProcessEngineClient.start()` bounded the `Handshake` wait with the client's
`request_timeout_s` (default 3.0 s, 1.5 s in the test fixtures). That value
bounds post-connection runtime requests, but the handshake is a startup
operation: the spawned child must boot the interpreter, import the full node
stack (NumPy, OpenCV, PyAV, RTMidi, sounddevice), and initialise the server
before it can acknowledge.

On Windows machines that startup cost is not merely interpreter warm-up: a
cold page cache and antivirus scanning of the spawned process and every DLL it
loads routinely push it past a 1.5–3 s deadline, especially under load. The
observable failure is `TimeoutError: Timed out waiting for Handshake` — seen
in the full test suite on Windows hosts and in packaging builds, landing on a
different spawn-heavy test per run while the same tests pass in isolation and
on Linux CI. The child is healthy; the deadline was simply shorter than
reality, so the test suite (and, with the 3.0 s default, potentially the
application on heavily loaded machines) declared a healthy engine dead.

## Decision

The handshake uses its own budget, decoupled from the request budget:

1. `ProcessEngineClient` gains a `startup_timeout_s` keyword
   (`DEFAULT_STARTUP_TIMEOUT_S = 15.0`) that bounds only the handshake in
   `start()` (initial start and restart). `request_timeout_s` continues to
   bound all post-connection requests; the activation, heartbeat, and close
   budgets are unchanged.
2. The 15 s default is generous enough for the worst supported startup path
   (Windows x64 with active antivirus and a cold cache) while still bounding a
   genuinely stuck child: a child that cannot answer a handshake in 15 s will
   not answer a 3 s one.
3. `app.shell.process_client_factory` forwards an optional
   `startup_timeout_s` override, mirroring the existing timeout knobs. The
   production shell (no overrides) uses the new default.
4. No wire-contract change: the `Handshake`/`HandshakeAcknowledged` messages
   and `ENGINE_PROTOCOL_VERSION` are untouched — this is a parent-side
   deadline only.

## Consequences

- Engine startup no longer fails on slow or antivirus-heavy Windows machines,
  and test fixtures that tighten the request budget for speed can no longer
  starve the handshake; the packaging test gate is stable on both platforms.
- A genuinely hung engine child now takes up to the startup budget to be
  declared failed instead of the request budget. Startup-failure handling is
  unchanged: the client reports `CRASHED` with an "Engine startup failed"
  last error and reaps the child.
- Callers that pass an explicit small `startup_timeout_s` (tests exercising
  the startup-timeout path) retain full control of the deadline.

## References

- `docs/adr/0005-ui-engine-process-separation.md` (process boundary the
  handshake protects)
- `src/synesthesia_machine/runtime/engine_client.py` (`ProcessEngineClient.start`)
- `src/synesthesia_machine/app/shell.py` (`process_client_factory`)
