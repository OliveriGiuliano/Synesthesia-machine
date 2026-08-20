# Runtime package

Immutable execution plans, deterministic in-process scheduling, and the temporary
in-process engine facade.

Live image/channel preview conversion runs on a bounded latest-result worker: one active result and
one replaceable pending result. Jobs are stamped with the graph-preview generation so plan activation
cannot publish stale data, and explicit engine idle/close operations include the preview worker.

## Public imports

Use `synesthesia_machine.runtime` for `ExecutionPlan`, `CompiledNode`, `PortKey`,
`Scheduler`, `TickResult`, and `EngineFacade`. `EngineFacade` is a lazy public export so
the graph compiler can import execution-plan types without an import cycle.

## Dependency direction

Execution-plan values depend on contracts and node definitions. The scheduler consumes
plans and invokes node runtimes. `EngineFacade` is the integration edge that depends on
the graph compiler; lower runtime modules must not depend on graph authoring,
persistence, Qt, or application modules.
