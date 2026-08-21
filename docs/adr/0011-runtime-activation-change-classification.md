# ADR 0011: Runtime activation change classification

- Status: Accepted
- Date: 2026-08-20

## Context

The graph document revision includes both executable state and persisted presentation state. The
editor previously scheduled engine activation from its general document-change signal, so releasing
a node drag or editing a group replaced the active runtime generation. Successful replacement then
correctly cleared retained preview payloads and sequence cursors, producing a visible flash in every
live connection pill despite no executable graph change.

## Decision

`DocumentSession.changed` continues to report every persisted edit for scene synchronization,
validation, dirty state, and autosave. A separate `DocumentSession.runtimeChanged` signal schedules
engine activation only when an edit can affect compiled execution or demand roots.

Structural node and connection edits, parameter changes, document replacement, and connection-pill
visibility changes are runtime-affecting. Node positions and canvas groups/comments are
presentation-only and do not schedule engine activation. Explicit preview-dock visibility changes
continue to schedule activation because they change demand roots.

The safe default for new commands is runtime-affecting; presentation-only commands must opt into the
presentation callback explicitly.

## Consequences

- Moving nodes or editing groups remains persisted, undoable, dirty-state-aware, and autosaved without
  resetting runtime nodes or briefly emptying live previews.
- The document revision may advance beyond the active engine revision after presentation-only edits;
  the next runtime-affecting activation uses the latest complete snapshot revision.
- Successful runtime activation and engine restart still clear all prior-generation previews as
  required by ADR 0008 and ADR 0009. Rejected activation still preserves the working generation.
- Graph schema and engine protocol versions do not change.

## References

- `docs/adr/0005-ui-engine-process-separation.md`
- `docs/adr/0008-per-connection-live-previews.md`
- `docs/adr/0009-bounded-asynchronous-preview-conversion.md`
- `docs/architecture/master.md`
