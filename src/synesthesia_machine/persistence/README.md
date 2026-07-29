# Persistence package

Schema-versioned graph JSON conversion, pure migrations, and atomic file I/O.

## Public imports

Use `synesthesia_machine.persistence` for `graph_to_data`, `graph_to_json`,
`graph_from_data`, `graph_from_json`, `save_graph`, `load_graph`, migration functions,
`GRAPH_SCHEMA_VERSION`, and `GraphPersistenceError`.

## Dependency direction

Persistence depends on graph snapshots, contracts, the application version, and
`NodeRegistry` for load-time type/version validation. Graph, nodes, and runtime code
must not import persistence. Migrations remain pure JSON-dictionary transformations;
compatibility branches do not belong in runtime node implementations.
