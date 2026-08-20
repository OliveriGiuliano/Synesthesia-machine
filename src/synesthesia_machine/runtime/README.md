# Runtime package

Immutable execution plans, deterministic scheduling, engine process client/server adapters, and
engine-owned device catalogues.

Live image/channel preview conversion runs on a bounded latest-result worker: one active result and
one replaceable pending result. Jobs are stamped with the graph-preview generation so plan activation
cannot publish stale data, and explicit engine idle/close operations include the preview worker.

## Public imports

Use `synesthesia_machine.runtime` for `ExecutionPlan`, `CompiledNode`, `PortKey`,
`Scheduler`, `TickResult`, and `EngineFacade`. `EngineFacade` is a lazy public export so
the graph compiler can import execution-plan types without an import cycle.

`ProcessEngineClient` is the production UI boundary. `InProcessEngineClient` is the child-owned
implementation and test seam; it is not permission to move hardware work back into the UI process.

## Dependency direction

Execution-plan values depend on contracts and node definitions. The scheduler consumes
plans and invokes node runtimes. `EngineFacade` is the integration edge that depends on
the graph compiler; lower runtime modules must not depend on graph authoring,
persistence, Qt, or application modules.
