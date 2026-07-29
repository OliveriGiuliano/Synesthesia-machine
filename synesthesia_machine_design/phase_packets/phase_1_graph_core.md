# Phase 1 — Contracts, graph model, and headless compiler

## Objective

Implement the typed graph and deterministic execution core without Qt UI, real video, camera, audio, or MIDI ports.

## Required contracts

- Graphs are DAGs. Cycles are invalid.
- One connection per ordinary input; unlimited output fan-out.
- Port types: IMAGE, CHANNEL, FLOAT, INT, BOOL, COLOR, MIDI_STATE, STRING. Only INT→FLOAT is implicit.
- Generic type variables exist only for Pass Through and Conditional and resolve at compile time.
- `NoData` is a singleton distinct from `None`.
- Inputs are immutable by contract.
- Every dynamic value carries one source `clock_id`; dynamic inputs may combine only when clock IDs match.
- A node executes at most once per tick even when fanned out.
- Static subgraphs cache until invalidated.

## Allowed paths

`contracts/`, `graph/`, `runtime/execution_plan.py`, `runtime/scheduler.py`, `nodes/base.py`, `nodes/registry.py`, `nodes/utility/`, `persistence/schemas.py`, `persistence/graph_io.py`, and relevant tests/docs.

## Public interfaces to implement first

- runtime dataclasses: FrameContext, ImageFrame, ChannelFrame, ColorValue, MidiStateFrame, NoData;
- NodeDefinition, port/parameter specifications, NodeRuntime protocol;
- GraphDocument/GraphSnapshot/NodeModel/ConnectionModel;
- NodeRegistry;
- ValidationReport and structured errors;
- GraphCompiler → immutable ExecutionPlan;
- in-process EngineFacade used later by EngineClient.

## Ordered tasks

1. Implement runtime value contracts with validation helpers and read-only-array checks.
2. Implement stable node/port/parameter IDs and node-definition registry.
3. Implement graph models independent of Qt.
4. Implement type compatibility, generic unification, connection cardinality, required-input checks, cycle detection, and clock-domain analysis.
5. Implement deterministic topological compilation and demand roots.
6. Implement per-tick output cache, static cache, NoData propagation, structured node errors, and per-node timing hooks.
7. Implement Number, Pass Through, Conditional, Compare, Logic Operation, and scalar Math nodes.
8. Implement schema-versioned JSON round trip and pure migration framework.
9. Add package READMEs documenting public imports and dependency direction.

## Required tests

- all type compatibility combinations;
- generic resolution success/failure;
- cycle detection including self-loop;
- fan-out executes upstream once;
- static cache invalidates on literal/connection change;
- NoData propagation;
- clock mismatch rejection;
- expected node error becomes NoData without terminating a tick;
- JSON round trip, unknown type/version handling, migration fixtures;
- deterministic results for synthetic ticks.

## Exit criteria

A headless test can construct, serialize, compile, and execute a utility graph over multiple synthetic ticks. Invalid graphs return actionable structured errors. No Qt import appears in these packages.

## Completion report

Implemented; interfaces; tests/results; limitations; architecture deviations; next work.
