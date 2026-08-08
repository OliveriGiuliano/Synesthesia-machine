"""Deterministic in-process execution with per-tick and static output caches."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from synesthesia_machine.contracts.engine_client import MidiOutputStatus, NodeMemoryDiagnostic
from synesthesia_machine.contracts.runtime_values import FrameContext, NoData, RuntimeValue
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    ExpectedNodeError,
    MidiOutputStatusProvider,
    NodeExecutionError,
    NodeMemoryDiagnosticProvider,
    NodeRuntime,
    PanicCapableRuntime,
    ResetReason,
)
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey, ScalarConversion

type TimingHook = Callable[[UUID, int], None]
type ProfilingHook = Callable[[UUID, int, Mapping[str, RuntimeValue], bool], None]


@dataclass(frozen=True, slots=True)
class TickResult:
    values: Mapping[PortKey, RuntimeValue]
    errors: tuple[NodeExecutionError, ...]
    invocation_counts: Mapping[UUID, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(
            self, "invocation_counts", MappingProxyType(dict(self.invocation_counts))
        )


class Scheduler:
    def __init__(
        self,
        plan: ExecutionPlan,
        *,
        timing_hook: TimingHook | None = None,
        profiling_hook: ProfilingHook | None = None,
        reusable_runtimes: Mapping[UUID, NodeRuntime] | None = None,
    ) -> None:
        self.plan = plan
        self._timing_hook = timing_hook
        self._profiling_hook = profiling_hook
        reusable = reusable_runtimes or {}
        self._runtimes: dict[UUID, NodeRuntime] = {}
        self._borrowed_runtime_ids: set[UUID] = set()
        self._static_cache: dict[PortKey, RuntimeValue] = {}
        try:
            for node in plan.nodes:
                runtime = reusable.get(node.node_id)
                if runtime is None:
                    runtime = node.definition.runtime_factory(node.node_id)
                else:
                    self._borrowed_runtime_ids.add(node.node_id)
                self._runtimes[node.node_id] = runtime
        except Exception:
            self.cancel_prepared()
            raise

    @classmethod
    def prepare_replacement(
        cls,
        plan: ExecutionPlan,
        previous: Scheduler | None,
        *,
        timing_hook: TimingHook | None = None,
        profiling_hook: ProfilingHook | None = None,
        preserve_state: bool = True,
    ) -> Scheduler:
        reusable: dict[UUID, NodeRuntime] = {}
        if previous is not None and preserve_state:
            previous_nodes = {node.node_id: node for node in previous.plan.nodes}
            invalidated_clocks = {
                node.clock_id
                for node in plan.nodes
                if node.definition.execution_kind is ExecutionKind.SOURCE
                and (
                    (old := previous_nodes.get(node.node_id)) is None
                    or old.state_retention_key != node.state_retention_key
                )
            }
            for node in plan.nodes:
                old = previous_nodes.get(node.node_id)
                if (
                    old is not None
                    and old.state_retention_key == node.state_retention_key
                    and node.clock_id not in invalidated_clocks
                    and node.node_id in previous._runtimes
                ):
                    reusable[node.node_id] = previous._runtimes[node.node_id]
        return cls(
            plan,
            timing_hook=timing_hook,
            profiling_hook=profiling_hook,
            reusable_runtimes=reusable,
        )

    @property
    def borrowed_runtime_ids(self) -> frozenset[UUID]:
        return frozenset(self._borrowed_runtime_ids)

    def set_profiling_hook(self, hook: ProfilingHook | None) -> None:
        self._profiling_hook = hook

    def execute_tick(
        self, context: FrameContext, *, source_values: Mapping[PortKey, RuntimeValue] | None = None
    ) -> TickResult:
        values = dict(self._static_cache)
        values.update(source_values or {})
        errors: list[NodeExecutionError] = []
        invocations: dict[UUID, int] = {}
        for node in self.plan.nodes:
            outputs_are_cached = bool(node.output_types) and all(
                PortKey(node.node_id, port_id) in values for port_id in node.output_types
            )
            if not node.is_demanded or outputs_are_cached:
                continue
            inputs: dict[str, RuntimeValue] = {}
            for port_id, binding in node.input_bindings.items():
                value = values.get(binding.source, NoData)
                if (
                    binding.conversion is ScalarConversion.INT_TO_FLOAT
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                ):
                    value = float(value)
                inputs[port_id] = value
            if not node.definition.handles_no_data and any(
                value is NoData for value in inputs.values()
            ):
                outputs = {port_id: NoData for port_id in node.output_types}
            else:
                collect_timing = self._timing_hook is not None or self._profiling_hook is not None
                started = time.perf_counter_ns() if collect_timing else None
                failed = False
                outputs: dict[str, RuntimeValue] = {}
                invocations[node.node_id] = invocations.get(node.node_id, 0) + 1
                try:
                    outputs = dict(
                        self._runtimes[node.node_id].process(
                            MappingProxyType(inputs), node.parameters, context
                        )
                    )
                except ExpectedNodeError as error:
                    failed = True
                    errors.append(
                        NodeExecutionError(
                            node.node_id,
                            error.code,
                            error.message,
                            error.details,
                            True,
                            context.tick_index,
                        )
                    )
                    outputs = {port_id: NoData for port_id in node.output_types}
                except Exception as error:  # Scheduler boundary converts unexpected node failures.
                    failed = True
                    errors.append(
                        NodeExecutionError(
                            node.node_id,
                            "unexpected_node_error",
                            str(error),
                            traceback.format_exc(),
                            False,
                            context.tick_index,
                        )
                    )
                    outputs = {port_id: NoData for port_id in node.output_types}
                finally:
                    if started is not None:
                        duration_ns = time.perf_counter_ns() - started
                        if self._timing_hook is not None:
                            self._timing_hook(node.node_id, duration_ns)
                        if self._profiling_hook is not None:
                            self._profiling_hook(node.node_id, duration_ns, outputs, failed)
            for port_id in node.output_types:
                values[PortKey(node.node_id, port_id)] = outputs.get(port_id, NoData)
            if node.is_static:
                for port_id in node.output_types:
                    key = PortKey(node.node_id, port_id)
                    self._static_cache[key] = values[key]
        return TickResult(values, tuple(errors), invocations)

    def reset_source(self, clock_id: UUID, reason: ResetReason) -> None:
        """Reset state-owning runtimes in one compiled source component."""

        state_owners = {
            ExecutionKind.SOURCE,
            ExecutionKind.STATEFUL,
            ExecutionKind.SINK,
            ExecutionKind.VISUALIZER,
        }
        for node in self.plan.nodes:
            if node.clock_id == clock_id and node.definition.execution_kind in state_owners:
                self._runtimes[node.node_id].reset(reason)

    def reset_all(self, reason: ResetReason) -> None:
        for runtime in self._runtimes.values():
            runtime.reset(reason)
        self._static_cache.clear()

    def panic(self) -> None:
        for runtime in self._runtimes.values():
            if isinstance(runtime, PanicCapableRuntime):
                runtime.panic()

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        statuses: list[MidiOutputStatus] = []
        for node_id, runtime in self._runtimes.items():
            if output_node_id is not None and node_id != output_node_id:
                continue
            if isinstance(runtime, MidiOutputStatusProvider):
                statuses.append(runtime.midi_output_status())
        return tuple(sorted(statuses, key=lambda status: str(status.node_id)))

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        diagnostics: list[NodeMemoryDiagnostic] = []
        for runtime_node_id, runtime in self._runtimes.items():
            if node_id is not None and runtime_node_id != node_id:
                continue
            if isinstance(runtime, NodeMemoryDiagnosticProvider):
                diagnostics.append(runtime.node_memory_diagnostic())
        return tuple(sorted(diagnostics, key=lambda diagnostic: str(diagnostic.node_id)))

    def claim_borrowed_runtimes(self, previous: Scheduler) -> None:
        """Transfer prepared shared runtimes after reaching the atomic swap boundary."""

        for node_id in self._borrowed_runtime_ids:
            runtime = previous._runtimes.get(node_id)
            if runtime is not self._runtimes[node_id]:
                raise RuntimeError(f"Prepared runtime {node_id} no longer matches the active plan")
        for node_id in self._borrowed_runtime_ids:
            previous._runtimes.pop(node_id)
        self._borrowed_runtime_ids.clear()

    def cancel_prepared(self) -> None:
        """Dispose candidate-owned runtimes without touching borrowed active instances."""

        for node_id, runtime in tuple(self._runtimes.items()):
            if node_id not in self._borrowed_runtime_ids:
                with suppress(Exception):
                    runtime.close()
        self._runtimes.clear()
        self._borrowed_runtime_ids.clear()
        self._static_cache.clear()

    def close(self, reason: ResetReason | None = None) -> None:
        for runtime in self._runtimes.values():
            if reason is not None:
                with suppress(Exception):
                    runtime.reset(reason)
            with suppress(Exception):
                runtime.close()
        self._runtimes.clear()
        self._borrowed_runtime_ids.clear()
        self._static_cache.clear()
