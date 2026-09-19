# ADR 0026: One engine body, two placements

- Status: Accepted
- Date: 2026-09-19
- Context: ADR-0005 (UI/engine process separation), ADR-0009 (preview broker
  and shared-memory transport), ADR-0022 (publish-generation panic ordering),
  the engine driver-surface shrink (architecture-deepening ticket 01)

## Context

The in-process engine placement and the spawned child both needed the same
orchestration — facade, frame worker, source lifecycle, preview broker and
cursors, profiling — and both got it from one class:
`InProcessEngineClient`. The class implemented the 26-method `EngineClient`
protocol, but the two placements use different slices of it: the in-process
UI driver reaches every method except `clear_previews`, while the child's
command dispatch and event publisher reach 20 of the 26 — `status()`,
`restart()`, `next_image_previews()`, `clear_previews()` and the per-family
preview-cursor resets have no protocol 16 command and are dead in the child.
The class was therefore two modules in one: its effective interface
depended on the placement, and the child-side coupling the code admitted in
a comment ("the child event publisher drives this client directly … the
reset must live here, not in a wrapper") was an unnamed seam.

## Decision

- **The engine body is its own module.** `EngineBody`
  (`runtime/engine_body.py`) owns the placement-independent orchestration:
  the facade, the latest-frame graph worker, the source controllers, the
  preview broker over the placement's `PreviewTransport`, the "already
  delivered" preview cursors, profiling, and the state/plan memory. It is
  what the spawned child runs behind the wire and what the in-process
  placement composes; the frame worker, source factories, and null preview
  broker live with it.
- **The in-process placement is body + protocol hat.**
  `InProcessEngineClient` keeps the `EngineClient` protocol surface and
  holds no engine state: it wraps an `EngineBody` and delegates every
  capability to it. Its client-only behaviours are `status()` (a
  connected-or-closed `EngineStatus`), `restart()` (re-activation from the
  body's last valid plan under `ENGINE_RESTARTED`), and `clear_previews()`.
- **The child placement runs the body directly.** `EngineServer` constructs
  `EngineBody(create_builtin_registry(), preview_transport=<shared-memory
  writer>)` and dispatches protocol commands onto the body. Its
  `_EventPublisher` is the named preview seam: it pulls
  `body.next_note_previews()` / `body.next_value_previews()` (the body owns
  the cursors) and ships the compact events. Because the body resets its
  cursors on activation itself, no wrapper exists that could forget to
  reset them in the child placement.
- **The protocol surface never appears in the child-facing modules.**
  `engine_body.py` and `engine_server.py` carry no `EngineClient` protocol
  methods, so the child's liveness is placement-static: every method they
  expose is one the child actually runs.

## Consequences

- A reader of `in_process_engine.py` meets one role per file: the
  in-process protocol hat. A reader of `engine_body.py` meets the engine
  body; its interface no longer changes with the placement.
- A future protocol command maps onto a body method; a future
  in-process-only capability is added to the client hat without touching
  the body the child runs.
- `EngineBody` is the natural host for future placement-independent engine
  capabilities (the preview publication seam already lives there).
- The spawned child still runs the full engine (facade, scheduler,
  sources, previews) — ADR-0005's process boundary is unchanged; only the
  in-process file split and the seam naming change.
- The in-process client's old "Engine client is closed" guard message
  becomes "Engine body is closed" where the body raises it; the process
  client keeps its own wording.

## References

- `src/synesthesia_machine/runtime/engine_body.py` (new)
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/runtime/__init__.py`
- `docs/adr/0005-ui-engine-process-separation.md`
