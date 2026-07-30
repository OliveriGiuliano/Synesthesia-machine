"""Deterministic in-process execution with per-tick and static output caches."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from synesthesia_machine.contracts.runtime_values import FrameContext, NoData, RuntimeValue
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    ExpectedNodeError,
    NodeExecutionError,
    NodeRuntime,
    ResetReason,
)
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey, ScalarConversion

type TimingHook = Callable[[UUID, int], None]


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
    def __init__(self, plan: ExecutionPlan, *, timing_hook: TimingHook | None = None) -> None:
        self.plan = plan
        self._timing_hook = timing_hook
        self._runtimes: dict[UUID, NodeRuntime] = {
            node.node_id: node.definition.runtime_factory(node.node_id) for node in plan.nodes
        }
        self._static_cache: dict[PortKey, RuntimeValue] = {}

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
                started = time.perf_counter_ns()
                invocations[node.node_id] = invocations.get(node.node_id, 0) + 1
                try:
                    outputs = dict(
                        self._runtimes[node.node_id].process(
                            MappingProxyType(inputs), node.parameters, context
                        )
                    )
                except ExpectedNodeError as error:
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
                    if self._timing_hook is not None:
                        self._timing_hook(node.node_id, time.perf_counter_ns() - started)
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

    def close(self) -> None:
        for runtime in self._runtimes.values():
            runtime.close()
        self._runtimes.clear()
        self._static_cache.clear()
