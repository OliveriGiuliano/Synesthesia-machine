# ADR 0014: Statistics combines many connected values or a Buffer through variadic array inputs

- Status: Accepted
- Date: 2026-09-14

## Context

The Statistics node reduced a single `ValueArray` (its `values` input, typed `T[]`) to one
element-wise statistic. The only way to build that array was a Buffer node, which samples one
source over time. There was no way to reduce several *distinct* sources at one instant — for
example the mean of two image frames produced by different branches, or a statistic over a mix
of live values and a short history — because an ordinary input accepts at most one connection
and the single `values` port had no further sockets to fan into.

## Decision

Statistics replaces its single `values` input with a variadic `values_1`, `values_2`, ...
socket family (each socket typed `T[]`, at least one required) and bumps
`implementation_version` to 2. Every socket accepts either a lone element of the underlying
type `T` or a Buffer's `ValueArray`; the runtime flattens each connected value (a single
scalar/image/channel, or the elements of a `ValueArray`) into one sample sequence and computes
the statistic across that sequence. The output stays the element type `T`, so a statistic over
a Buffer of images is still one image.

To make a lone element type-check into an array-typed socket, the compiler's compatibility
matrix gains one non-converting rule: a single element may feed the array type of its own
family (`FLOAT`/`INT` -> `SCALAR_ARRAY`, `IMAGE` -> `IMAGE_ARRAY`, `CHANNEL` ->
`CHANNEL_ARRAY`). Unlike `INT -> FLOAT` this inserts no scalar conversion; the runtime simply
treats the lone value as a one-item batch. The reverse direction (an array into a single-value
socket) remains rejected.

Graph schema version moves 2 -> 3: saved connections that target the legacy Statistics `values`
port are rewritten to `values_1`, and Statistics nodes advance from implementation version 1 to
2 (a no-op payload migration). The catalogue and bundled example graphs are re-serialized at the
new schema.

## Consequences

- One Statistics node now accepts several direct value/image/channel connections, a Buffer
  output, or any mix of them, all sharing the same source clock (the existing clock rule).
- Existing buffer -> Statistics graphs keep working after the v2 -> v3 migration; graphs that
  predate the schema still load because the migration is pure and sequential.
- A Statistics node with no connection is invalid (at least one socket of the
  `values_1`, `values_2`, ... family is required), matching the variadic contract already
  used by MIDI Merge.
- No engine protocol, scheduler, or process-placement change: the node stays stateless and the
  scheduler already delivers every bound socket into the runtime's input mapping.

## Correction (2026-09-16)

The original implementation enforced the variadic minimum socket-by-socket: sockets
`1..minimum_count` were each individually required, so a Statistics node with only
`values_2` connected (and `values_1` freed) was reported `required_input_missing` and
auto-stopped the engine even though the family minimum of one connected socket was met.
That contradicted this ADR's "at least one required" family-level contract. The compiler
now enforces the minimum at family level (any `minimum_count` of the family's sockets)
and emits a single family-level `required_input_missing` issue when the count is short;
`NodeDefinition.required_inputs` reports the family prefix as a synthetic marker for
variadic families. No schema, protocol, or serialization change: saved graphs are
unaffected.

## References

- `docs/adr/0005-ui-engine-process-separation.md`
- `docs/architecture/master.md` (sections 7.1 and 7.2)
- `src/synesthesia_machine/graph/compiler.py` (`types_compatible`)
- `src/synesthesia_machine/persistence/schemas.py` (v2 -> v3)
