# ADR 0013: Engine auto-stops when the graph is broken

- Status: Accepted
- Date: 2026-09-13

## Context

When the editor activated a graph revision that failed validation or compilation, the engine
kept running the previous valid plan and reported "previous valid runtime remains active". While
that preserved a working generation, it let audio, MIDI note states, and source playback keep
being produced from a graph the user can no longer see or edit coherently: a broken document
(a deleted node, an incompatible reconnection, an unknown type) silently kept the old sound
going, and users could not tell that the live output no longer matched their graph.

ADR 0011 stated that "rejected activation still preserves the working generation". This ADR
supersedes that one consequence: rejection now stops the runtime instead of preserving it.

## Decision

When an engine activation compiles to no plan (the document is broken), the engine transitions
to the stopped state:

- sources are stopped and closed,
- the graph worker is drained and closed,
- the active scheduler panics MIDI outputs (all notes off) and then closes its node runtimes,
- retained previews are cleared,
- the engine client reports `EngineState.STOPPED` and stays open and responsive.

The transition is a deliberate, atomic state change owned by the engine client (and therefore
applies identically to the in-process client and the child engine process, which reuses the
same client). The last *valid* snapshot is still remembered, so `restart()` after an engine
crash recovers the last valid graph rather than a broken one.

When the graph becomes valid again, the editor's debounced activation starts the engine
automatically; no manual re-activation is required.

The compile-prepare-commit rule for *valid* candidates is unchanged: a valid replacement is
still prepared off the active path and swapped atomically, and a plan-swap failure still leaves
the working generation untouched. Stopping on a broken graph is a separate, documented state
transition, not a failed replacement.

## Consequences

- A broken graph can no longer keep producing audio or MIDI output from stale state; outputs
  are silenced (MIDI panic) as part of the stop.
- Users see a status message ("Graph has N error(s); engine stopped") and the validation issues
  panel; fixing the graph resumes the engine on the next debounced activation.
- Transient edit states that briefly fail validation (for example mid-connection gestures) can
  stop and restart the runtime; the 100 ms activation debounce and the atomic swap keep this
  bounded, and the previous trade-off (stale output vs. transient silence) now resolves in
  favour of never running output the document does not describe.
- `restart()` and crash recovery semantics are unchanged (last valid snapshot is used).
- The engine protocol and graph schema do not change; the activation acknowledgement already
  carries the validation report.

## References

- `docs/adr/0005-ui-engine-process-separation.md`
- `docs/adr/0011-runtime-activation-change-classification.md`
- `docs/architecture/master.md` (sections 9.5 and the plan-replacement risk mitigation)
