# ADR 0029: Engine keeps running on a partially valid graph

- Status: Accepted
- Date: 2026-09-21

## Context

ADR 0013 stops the engine whenever an activation compiles to no plan: any single validation
error anywhere in the document (a deleted node, a mismatched reconnection, an unknown type)
tears down the whole runtime — sources are stopped and closed, MIDI outputs panic, previews
clear, state becomes `STOPPED`.

The validation pipeline already localizes most errors (bad connections are dropped from the
valid set, cyclic nodes are dropped from the topological order, unknown-type nodes are dropped
from the definitions), but the final all-or-nothing gate turns every error into "no plan". In
practice this makes the instrument too brittle for live editing: spawning a lone unconnected
node (a processor with an unconnected required input is the most common case) invalidates the
whole document and silently stops a performance that was running correctly a moment earlier.
Only the tiny broken fragment should go quiet; the rest of the graph should keep producing
signal.

## Decision

This ADR supersedes the ADR 0013 stop condition (rejection stops the runtime) with a
partial-validity rule. ADR 0013's safety property is preserved: the engine never keeps
producing output from a document region the user no longer controls — but the region is now
the *invalid parts*, not the whole document. ADR 0020 (auto-resume of playing sources after a
broken-graph stop) is unchanged: it applies to the remaining, rarer, full-stop case.

1. **Partial compilation.** When the document's validation report contains errors, the
   compiler isolates the maximal valid remainder instead of refusing a plan:

   - nodes are excluded when an error attaches to them — including input
     cardinality violations, since a double-fed socket cannot be isolated, so
     the consumer is excluded — or any of their ports stayed unsettled by type
     resolution;
   - connections are excluded when an error attaches to them (missing endpoint, unknown
     port, incompatible types, duplicate IDs) or an endpoint is excluded;
   - the exclusion is re-validated to a fixed point, so a downstream node that loses a
     required input because its producer was excluded is excluded in turn;
   - explicitly requested demand roots that were excluded are dropped from the demand set.

   The plan is built over the reduced subgraph, stamped with the original snapshot's
   `document_id` and `revision`. The validation report returned with the activation is the
   one for the *whole* document: every error stays visible (validation panel, status
   message) even though the affected parts do not run. For a fully valid document the
   behavior is exactly what it was before this ADR.

2. **Keep-running rule.** The engine keeps a new (partially valid) plan running whenever the
   plan contains at least one source node; it stops (the full ADR 0013 teardown: sources
   stopped and closed, worker drained, MIDI panic, previews cleared, `STOPPED`) only when
   compilation yields no plan at all, or when no source survives into the plan — in which
   case no input can reach any output, so no signal can traverse the graph.

3. **Invalid parts do not propagate signal.** Excluded nodes are absent from the plan: they
   produce nothing, consume no ticks, and their former consumers either run with the missing
   input (optional sockets) or are themselves excluded (required sockets, fixed point).

4. **Lifecycle of a partial activation is the ordinary plan-swap lifecycle:** compatible
   runtimes and sources are preserved by the existing state-retention keys, excluded
   runtimes are closed with the old plan, previews renumber with the generation bump, and
   the `EngineState` rules are unchanged. The activation acknowledgement is unchanged
   (`graph_revision`, report, `activated`): `activated=True` now means "the engine is
   running this snapshot's valid remainder", and the carried report may list the isolated
   errors. No engine-protocol, graph-schema, or `ENGINE_PROTOCOL_VERSION` change.

## Consequences

- A working performance survives the user spawning, dragging, or miswiring isolated parts of
  the graph: the broken fragment goes quiet (its links lose their previews, a broken chain
  upstream of a MIDI output stops emitting) while the valid signal paths keep producing
  audio, notes, and previews. Fixing or deleting the broken fragment restores those paths on
  the next debounced activation, without a stop/restart of the healthy parts.
- A graph whose sources all fall out of the valid remainder — or a source-less static
  graph — still stops the engine: with no source, no input can reach any output, so there
  is no signal to keep alive. A valid source-only graph (a source feeding nothing but still
  tickable) keeps running exactly as before.
- The engine can now be `RUNNING` while the validation panel lists errors; the status
  message distinguishes the three activation outcomes (clean activation, activation with
  isolated errors, stop). Batch consumers that require the whole document to be clean (MIDI
  export) keep their stricter precondition and are unaffected.
- Exclusion is deliberately conservative: a node sharing a type-variable group with an
  excluded node is excluded with it, even if it would resolve in isolation. The compiler
  never runs code whose port types the full document could not settle.
- Crash recovery and `restart()` re-activate the last *accepted* snapshot, which may now be
  only partially valid; the reduced plan is re-derived deterministically on restart.
- Tests must cover: a lone invalid node leaves a running engine running; breakage that
  removes every source stops the engine and later auto-resumes the remembered playing
  sources (ADR 0020); a source-less plan stops; the cascade exclusion (a removed producer
  invalidates a dependent consumer); and the report/ack still carries every document error.

## References

- `docs/adr/0013-engine-auto-stops-on-broken-graph.md` (stop condition superseded; the
  teardown sequence itself remains the one used for full stops)
- `docs/adr/0020-auto-resume-playing-sources-after-broken-graph-stop.md`
- `docs/architecture/master.md` (sections 9.3, 9.5, 9.6)
