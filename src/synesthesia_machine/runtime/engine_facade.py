"""In-process facade used until the process-based engine client is introduced."""

from collections.abc import Iterable, Mapping
from uuid import UUID

from synesthesia_machine.contracts.runtime_values import FrameContext, RuntimeValue
from synesthesia_machine.graph.compiler import CompilationResult, GraphCompiler
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey
from synesthesia_machine.runtime.scheduler import Scheduler, TickResult


class EngineFacade:
    def __init__(self, registry: NodeRegistry) -> None:
        self._compiler = GraphCompiler(registry)
        self._plan: ExecutionPlan | None = None
        self._scheduler: Scheduler | None = None

    @property
    def active_plan(self) -> ExecutionPlan | None:
        return self._plan

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> CompilationResult:
        result = self._compiler.compile(snapshot, demand_roots=demand_roots)
        if result.plan is None:
            return result
        replacement = Scheduler(result.plan)
        previous = self._scheduler
        self._plan = result.plan
        self._scheduler = replacement
        if previous is not None:
            previous.close()
        return result

    def tick(
        self, context: FrameContext, *, source_values: Mapping[PortKey, RuntimeValue] | None = None
    ) -> TickResult:
        if self._scheduler is None:
            raise RuntimeError("No valid graph plan is active")
        return self._scheduler.execute_tick(context, source_values=source_values)

    def close(self) -> None:
        if self._scheduler is not None:
            self._scheduler.close()
        self._scheduler = None
        self._plan = None
