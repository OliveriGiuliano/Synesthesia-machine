# ADR-0005: Separate UI and engine processes

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Decoder faults and heavy native processing must not block Qt's UI event loop.

## Decision

The final runtime uses a Qt-owned UI process and a spawned engine process. Control messages are
versioned dataclasses. Bounded shared memory carries throttled preview bytes; live float32 frames
remain engine-local.

## Consequences

Windows `spawn` compatibility constrains entry points and pickled contracts. Cleanup must be
idempotent after normal shutdown or forced termination. UI code cannot depend on an in-process
engine implementation detail.