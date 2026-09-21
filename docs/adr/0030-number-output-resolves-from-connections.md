# ADR 0030: Number output resolves from its connections

- Status: Accepted
- Date: 2026-09-21

## Context

The built-in Number node has moved twice: v1 exposed a type selector plus separate
`float_value`/`int_value` fields; v2 collapsed the fields into one `value` parameter
while keeping the selector; v3 removed the selector in favour of two dedicated outputs
(`value: FLOAT` and `int_value: INT`).

Dual outputs duplicate one literal across two ports, which the graph model treats as
two independent producers: a consumer that wants the integer form of the literal must
wire `int_value`, one that wants the float form wires `value`, and a consumer whose
socket is a type variable (Statistics, Buffer, Math) can only receive one of the two
concrete types. In practice most consumers are type-variable sockets, so the dual
outputs add wiring choices without adding capability, and the `int_value` port is
rarely the one used.

## Decision

Version 4 of `synmachine.utility.number` replaces both outputs with a single output
`value` whose declared type is a type variable `T` restricted to `{INT, FLOAT}` with
a default of `FLOAT`:

1. **Resolution from connections.** `T` resolves exactly like any other compile-time
   type variable: the union of the concrete types demanded by the connected consumer
   sockets settles it. A literal wired into an Integer context (e.g. a consumer with a
   fixed INT socket) settles `T` to Integer; into Float or mixed contexts it settles to
   Float.

2. **Lone or unconstrained literals settle to Float.** When no connection constrains
   `T` (a lone node, or a chain of type-variable sockets with no concrete consumer),
   the variable settles to its declared default, `FLOAT`. A default settles the whole
   unconstrained union group, so a lone literal feeding generic sockets such as Buffer
   or Statistics stays valid and settles those sockets to Float. A type variable
   without a default in an unconstrained group remains an `unresolved_generic_type`
   validation error, as before.

3. **Whole literals are emitted as integers.** The runtime emits the literal as an
   `int` when it is whole and as a `float` otherwise. A whole literal filling an
   Integer-declared output is accepted as-is; a whole literal whose output settled to
   Float is widened to float at the scheduler output boundary by the existing
   implicit INT -> FLOAT conversion, so it behaves exactly like any other int
   producer feeding a Float socket.

4. **Fractional values in Integer contexts are errors, not truncation.** A fractional
   literal whose output settled to Integer fails output validation
   (`invalid_node_output`) at runtime; the node never silently truncates. Authors who
   need integer semantics on a fractional literal use the Float to Integer node.

The v3-to-v4 migration is a payload identity: v4 stores the same `value` parameter, so
saved graphs migrate by version bump only.

## Consequences

- One wiring choice instead of two: the literal's type is inferred from where it is
  connected, and a lone literal is valid without any consumer.
- `implementation_version` moves 3 -> 4; the `int_value` output port is removed from
  the definition. Saved graphs whose `int_value` connection survives the identity
  migration fail validation with `unknown_output_port` and must be rewired onto
  `value` (or through a Float to Integer node when integer semantics are required);
  the graph remains loadable and every other part stays valid.
- The type-variable default mechanism is now part of the public node-authoring API:
  a variable's default settles its union group when no connection constrains it, and
  the compiler checks the default against the group's merged allowed set. Groups whose
  members declare conflicting defaults remain unresolved rather than settling
  direction-dependently.
- Static caching and previews are unaffected: both read the plan's resolved concrete
  output types, which are unchanged in kind (INT or FLOAT) from v3's dedicated ports.

## References

- `docs/architecture/master.md` (section 7.1 type variables, section 8.1 Number)
- `src/synesthesia_machine/nodes/utility/core.py` (definition, runtime, migration)
- `src/synesthesia_machine/graph/compiler.py` (group-default resolution)
