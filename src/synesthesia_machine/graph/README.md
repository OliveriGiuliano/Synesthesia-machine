# Graph package

Qt-independent graph authoring, validation, and deterministic compilation.

## Public imports

Use `synesthesia_machine.graph` for `GraphDocument`, `GraphSnapshot`,
`NodeModel`, `ConnectionModel`, `ValidationReport`, `GraphCompiler`,
`CompilationResult`, and `types_compatible`. `ValidationReport` and the
`LiteralValue` alias are owned by the contracts package (they cross the engine
process boundary inside engine activation) and re-exported here.

## Dependency direction

The graph model depends on contracts. The compiler additionally consumes immutable node
definitions and produces runtime execution-plan values. Graph code must not depend on
the scheduler, `EngineFacade`, persistence, Qt, or application modules. The runtime
package therefore exposes `EngineFacade` lazily to avoid reversing this dependency.
