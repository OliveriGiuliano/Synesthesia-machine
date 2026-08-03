"""Compile-prepare-commit facade for one child-owned runtime scheduler."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.contracts.engine_client import MidiOutputStatus, NodeMemoryDiagnostic
from synesthesia_machine.contracts.runtime_values import FrameContext, RuntimeValue
from synesthesia_machine.graph.compiler import CompilationResult, GraphCompiler
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.nodes.base import ResetReason
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey
from synesthesia_machine.runtime.scheduler import Scheduler, TickResult, TimingHook


@dataclass(slots=True)
class PreparedEngineActivation:
    """A compiled scheduler candidate that has not replaced the active plan yet."""

    result: CompilationResult
    scheduler: Scheduler | None
    committed: bool = False

    @property
    def plan(self) -> ExecutionPlan | None:
        return self.result.plan

    def close(self) -> None:
        if not self.committed and self.scheduler is not None:
            self.scheduler.cancel_prepared()
            self.scheduler = None


class EngineFacade:
    def __init__(self, registry: NodeRegistry, *, timing_hook: TimingHook | None = None) -> None:
        self._compiler = GraphCompiler(registry)
        self._timing_hook = timing_hook
        self._plan: ExecutionPlan | None = None
        self._scheduler: Scheduler | None = None

    @property
    def active_plan(self) -> ExecutionPlan | None:
        return self._plan

    def activate(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
        reset_reason: ResetReason = ResetReason.PLAN_REPLACED,
    ) -> CompilationResult:
        prepared = self.prepare(
            snapshot,
            demand_roots=demand_roots,
            reset_reason=reset_reason,
        )
        if prepared.plan is not None:
            self.commit(prepared, reset_reason=reset_reason)
        return prepared.result

    def prepare(
        self,
        snapshot: GraphSnapshot,
        *,
        demand_roots: Iterable[UUID] | None = None,
        reset_reason: ResetReason = ResetReason.PLAN_REPLACED,
    ) -> PreparedEngineActivation:
        result = self._compiler.compile(snapshot, demand_roots=demand_roots)
        if result.plan is None:
            return PreparedEngineActivation(result, None)
        replacement = Scheduler.prepare_replacement(
            result.plan,
            self._scheduler,
            timing_hook=self._timing_hook,
            preserve_state=reset_reason is ResetReason.PLAN_REPLACED,
        )
        return PreparedEngineActivation(result, replacement)

    def commit(
        self,
        prepared: PreparedEngineActivation,
        *,
        reset_reason: ResetReason = ResetReason.PLAN_REPLACED,
    ) -> None:
        replacement = prepared.scheduler
        if prepared.committed or replacement is None or prepared.plan is None:
            raise RuntimeError("Engine activation candidate is not available for commit")
        previous = self._scheduler
        if previous is not None:
            replacement.claim_borrowed_runtimes(previous)
        self._plan = prepared.plan
        self._scheduler = replacement
        prepared.committed = True
        prepared.scheduler = None
        if previous is not None:
            previous.close(reset_reason)

    def tick(
        self, context: FrameContext, *, source_values: Mapping[PortKey, RuntimeValue] | None = None
    ) -> TickResult:
        if self._scheduler is None:
            raise RuntimeError("No valid graph plan is active")
        return self._scheduler.execute_tick(context, source_values=source_values)

    def reset_source(self, source_node_id: UUID, reason: ResetReason) -> None:
        if self._scheduler is not None:
            self._scheduler.reset_source(source_node_id, reason)

    def reset_all(self, reason: ResetReason) -> None:
        if self._scheduler is not None:
            self._scheduler.reset_all(reason)

    def panic(self) -> None:
        if self._scheduler is not None:
            self._scheduler.panic()

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        if self._scheduler is None:
            return ()
        return self._scheduler.midi_output_status(output_node_id)

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        if self._scheduler is None:
            return ()
        return self._scheduler.node_memory_diagnostics(node_id)

    def close(self) -> None:
        if self._scheduler is not None:
            self._scheduler.close()
        self._scheduler = None
        self._plan = None


__all__ = ["EngineFacade", "PreparedEngineActivation"]
