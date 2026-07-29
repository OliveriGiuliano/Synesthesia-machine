# Nodes package

Stable node metadata, runtime protocols, registry behavior, and built-in utility nodes.

## Public imports

Use `synesthesia_machine.nodes` for node definitions, port/parameter specifications,
runtime/reset/error contracts, and `NodeRegistry`. Use
`synesthesia_machine.nodes.utility.create_utility_registry()` for the Phase 1 Number,
Pass Through, Conditional, Compare, Logic Operation, and scalar Math catalogue.

## Dependency direction

Node contracts depend only on runtime values from `contracts`. Concrete utility nodes
depend on those contracts and the node registry. Nodes must not import graph authoring,
the scheduler, persistence, Qt, or application modules.
