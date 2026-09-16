# Contracts package

Framework-independent values shared across process, graph, node, and runtime boundaries.

## Public imports

Import runtime values, validation values, and engine messages from
`synesthesia_machine.contracts`, not from implementation modules. The facade
exports `FrameContext`, `ImageFrame`, `ChannelFrame`, `ColorValue`,
`MidiStateFrame`, `NoData`, `PortType`, the `ParameterValue`/`LiteralValue`
aliases, the validation values (`ValidationReport`, `ValidationIssue`,
`ValidationSeverity`), and the versioned engine-message dataclasses. Device
catalogue descriptors keep engine-owned identifiers separate from UI labels,
represent partial/pending discovery explicitly, and are also transported only
through this facade.

## Dependency direction

`contracts` is the lowest-level domain package. It may depend on the standard
library and NumPy value representation, but it must not import Qt,
application, graph, node, runtime, or persistence code. A single documented
exception: `engine_client` references the graph `GraphSnapshot` type under
`TYPE_CHECKING` so the client protocol can name the snapshot it accepts; that
reference is type-only and creates no runtime dependency on graph.
