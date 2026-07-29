"""Deterministic test definitions for Phase 1 graph and scheduler behavior."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.contracts import FrameContext, ParameterValue, PortType, RuntimeValue
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ResetReason,
)


@dataclass(slots=True)
class RuntimeCounters:
    process: int = 0
    close: int = 0


class ProbeRuntime:
    def __init__(
        self,
        node_id: UUID,
        counters: dict[UUID, RuntimeCounters],
        *,
        output: RuntimeValue = 1.0,
        raise_expected: bool = False,
    ) -> None:
        self.node_id = node_id
        self._counters = counters
        self._output = output
        self._raise_expected = raise_expected
        counters[node_id] = RuntimeCounters()

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        self._counters[self.node_id].process += 1
        if self._raise_expected:
            raise ExpectedNodeError("expected_failure", "Synthetic expected failure")
        return {"value": inputs.get("value", self._output)}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        self._counters[self.node_id].close += 1


def make_definition(
    type_id: str,
    *,
    input_type: PortType | None = None,
    output_type: PortType = PortType.FLOAT,
    execution_kind: ExecutionKind = ExecutionKind.STATELESS,
    cache_policy: CachePolicy = CachePolicy.AUTO,
    handles_no_data: bool = False,
    counters: dict[UUID, RuntimeCounters] | None = None,
    output: RuntimeValue = 1.0,
    raise_expected: bool = False,
) -> NodeDefinition:
    runtime_counters = counters if counters is not None else {}

    def factory(node_id: UUID) -> ProbeRuntime:
        return ProbeRuntime(
            node_id,
            runtime_counters,
            output=output,
            raise_expected=raise_expected,
        )

    inputs = () if input_type is None else (InputPortSpec("value", "Value", input_type),)
    return NodeDefinition(
        type_id=type_id,
        implementation_version=1,
        display_name=type_id,
        category="Test",
        description="Test node definition.",
        inputs=inputs,
        outputs=(OutputPortSpec("value", "Value", output_type),),
        parameters=(),
        execution_kind=execution_kind,
        runtime_factory=factory,
        cache_policy=cache_policy,
        handles_no_data=handles_no_data,
    )


def frame_context(*, clock_id: UUID, tick_index: int = 1) -> FrameContext:
    return FrameContext(
        clock_id=clock_id,
        tick_index=tick_index,
        source_frame_index=tick_index - 1,
        source_time_s=(tick_index - 1) / 60.0,
        received_monotonic_ns=tick_index,
        deadline_monotonic_ns=None,
        is_realtime=False,
    )
