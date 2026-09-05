"""Deterministic in-process execution with per-tick and static output caches."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from synesthesia_machine.contracts.engine_client import MidiOutputStatus, NodeMemoryDiagnostic
from synesthesia_machine.contracts.runtime_values import (
    ChannelFrame,
    ColorValue,
    FrameContext,
    ImageFrame,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
    ValueArray,
    clock_id_of,
)
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
from synesthesia_machine.runtime.execution_plan import (
    CompiledNode,
    ExecutionPlan,
    PortKey,
    ScalarConversion,
)

type TimingHook = Callable[[UUID, int], None]
type ProfilingHook = Callable[[UUID, int, Mapping[str, RuntimeValue], bool], None]

_LOGGER = logging.getLogger("synesthesia_machine.engine")


class _InvalidNodeOutput(ValueError):
    pass


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
        # Built once per plan so the tick loop dispatches through precompiled
        # per-node validators instead of rebuilding sets and isinstance ladders.
        self._output_validators: list[_OutputValidator] = [
            _OutputValidator(node) for node in plan.nodes
        ]
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
        values = dict(self._static_cache) if self._static_cache else {}
        values.update(source_values or {})
        errors: list[NodeExecutionError] = []
        invocations: dict[UUID, int] = {}
        for node, validator in zip(self.plan.nodes, self._output_validators, strict=True):
            outputs_are_cached = bool(node.output_types)
            for output_key in node.output_port_keys:
                if output_key not in values:
                    outputs_are_cached = False
                    break
            if not node.is_demanded or outputs_are_cached:
                continue
            inputs: dict[str, RuntimeValue] = {}
            for port_id, source, conversion in node.input_binding_items:
                value = values.get(source, NoData)
                if (
                    conversion is ScalarConversion.INT_TO_FLOAT
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                ):
                    value = float(value)
                inputs[port_id] = value
            contains_no_data = False
            if not node.definition.handles_no_data:
                for value in inputs.values():
                    if value is NoData:
                        contains_no_data = True
                        break
            if contains_no_data:
                outputs = {port_id: NoData for port_id in node.output_types}
            else:
                collect_timing = self._timing_hook is not None or self._profiling_hook is not None
                started = time.perf_counter_ns() if collect_timing else None
                failed = False
                outputs: dict[str, RuntimeValue] = {}
                invocations[node.node_id] = invocations.get(node.node_id, 0) + 1
                try:
                    effective_parameters = _effective_parameters(node, inputs)
                    outputs = dict(
                        self._runtimes[node.node_id].process(
                            MappingProxyType(inputs),
                            MappingProxyType(effective_parameters),
                            context,
                        )
                    )
                    # The compiler can resolve a type variable to FLOAT without
                    # inserting a conversion (e.g. all-int Statistics samples
                    # feeding a FLOAT consumer); apply the one implicit
                    # concrete conversion (INT -> FLOAT) at the output
                    # boundary, mirroring how converted inputs are
                    # materialised above, so the emitted int is not rejected
                    # as a contract violation.
                    for output_port_id in node.float_output_ports:
                        output_value = outputs.get(output_port_id)
                        if isinstance(output_value, int) and not isinstance(output_value, bool):
                            outputs[output_port_id] = float(output_value)
                    validator.validate(outputs)
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
                except _InvalidNodeOutput as error:
                    failed = True
                    errors.append(
                        NodeExecutionError(
                            node.node_id,
                            "invalid_node_output",
                            str(error),
                            None,
                            False,
                            context.tick_index,
                        )
                    )
                    outputs = {port_id: NoData for port_id in node.output_types}
                except Exception as error:  # Scheduler boundary converts unexpected node failures.
                    failed = True
                    _LOGGER.exception(
                        "Unexpected node execution failure",
                        extra={"node_id": str(node.node_id), "tick_index": context.tick_index},
                    )
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
            for port_id, output_key in node.output_port_items:
                values[output_key] = outputs.get(port_id, NoData)
            if node.is_static:
                for _, output_key in node.output_port_items:
                    self._static_cache[output_key] = values[output_key]
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


def _effective_parameters(
    node: CompiledNode, inputs: dict[str, RuntimeValue]
) -> Mapping[str, ParameterValue]:
    """Overlay connected parameter values without mutating the compiled literal defaults.

    The connectable parameter specs are precomputed on the compiled node, so the
    tick loop never re-scans definition metadata. The parameter mapping is only
    copied once a connected override actually exists; otherwise the compiled
    read-only mapping is handed through without copying.
    """

    try:
        values: dict[str, ParameterValue] | None = None
        for parameter in node.connectable_parameter_specs:
            if parameter.id not in inputs:
                continue
            if values is None:
                values = dict(node.parameters)
            connected = parameter.connected_value(inputs[parameter.id])
            values[parameter.id] = connected
            # Legacy runtimes that explicitly consult the parameter input receive the same coerced
            # value as runtimes that read only the effective parameter mapping.
            inputs[parameter.id] = connected
        if values is None:
            return node.parameters
        if node.definition.parameter_validator is not None:
            errors = node.definition.parameter_validator(values)
            if errors:
                raise ValueError("; ".join(errors))
        return values
    except (TypeError, ValueError) as error:
        raise ExpectedNodeError(
            "invalid_dynamic_parameter",
            f"Invalid live parameter value: {error}",
        ) from error


_ARRAY_ITEM_TYPES: Mapping[PortType, frozenset[PortType]] = {
    PortType.SCALAR_ARRAY: frozenset({PortType.FLOAT, PortType.INT}),
    PortType.IMAGE_ARRAY: frozenset({PortType.IMAGE}),
    PortType.CHANNEL_ARRAY: frozenset({PortType.CHANNEL}),
}


def _output_type_check(expected: PortType) -> Callable[[RuntimeValue], bool]:
    """Compile the expected-type ladder for one port into a single predicate."""

    if expected is PortType.IMAGE:
        return lambda value: isinstance(value, ImageFrame)
    if expected is PortType.CHANNEL:
        return lambda value: isinstance(value, ChannelFrame)
    if expected is PortType.FLOAT:
        return lambda value: isinstance(value, float)
    if expected is PortType.INT:
        return lambda value: isinstance(value, int) and not isinstance(value, bool)
    if expected is PortType.BOOL:
        return lambda value: isinstance(value, bool)
    if expected is PortType.COLOR:
        return lambda value: isinstance(value, ColorValue)
    if expected is PortType.MIDI_STATE:
        return lambda value: isinstance(value, MidiStateFrame)
    if expected is PortType.STRING:
        return lambda value: isinstance(value, str)
    item_types = _ARRAY_ITEM_TYPES.get(expected)
    if item_types is not None:
        return lambda value, item_types=item_types: (
            isinstance(value, ValueArray) and value.item_type in item_types
        )
    return lambda value: False


class _OutputValidator:
    """Per-node output contract check precompiled once at plan-prepare time.

    Replaces the per-tick ``set(outputs) - set(output_types)`` difference, the
    expected-type isinstance ladder, and the repeated ``clock_id_of`` passes:
    the tick loop runs one membership test, one compiled type predicate per
    port, and a single clock pass per output (``clock_id_of`` runs at most
    once per output value; a ValueArray is walked exactly once).
    """

    __slots__ = ("clock_id", "port_checks", "port_types")

    def __init__(self, node: CompiledNode) -> None:
        self.clock_id = node.clock_id
        port_checks: dict[str, Callable[[RuntimeValue], bool]] = {}
        port_types: dict[str, PortType] = {}
        for port_id, expected_type in node.output_types.items():
            port_types[port_id] = expected_type
            port_checks[port_id] = _output_type_check(expected_type)
        self.port_checks = port_checks
        self.port_types = port_types

    def validate(self, outputs: Mapping[str, RuntimeValue]) -> None:
        unknown = tuple(sorted(port_id for port_id in outputs if port_id not in self.port_checks))
        if unknown:
            raise _InvalidNodeOutput(
                f"Runtime returned unknown output port(s): {', '.join(unknown)}"
            )
        expected_clock = self.clock_id
        for port_id, value in outputs.items():
            if value is NoData:
                continue
            if not self.port_checks[port_id](value):
                raise _InvalidNodeOutput(
                    f"Output {port_id!r} expected {self.port_types[port_id].value}, "
                    f"got {type(value).__name__}"
                )
            if isinstance(value, ValueArray):
                clocks: set[UUID] = set()
                for item in value.values:
                    item_clock = clock_id_of(item)
                    if item_clock is not None:
                        clocks.add(item_clock)
                if len(clocks) == 1:
                    (output_clock,) = clocks
                    if expected_clock is not None and output_clock != expected_clock:
                        raise _InvalidNodeOutput(
                            f"Output {port_id!r} uses clock {output_clock}, "
                            f"expected {expected_clock}"
                        )
                if len(clocks) > 1:
                    raise _InvalidNodeOutput(
                        f"Output {port_id!r} contains values from multiple source clocks"
                    )
                continue
            output_clock = clock_id_of(value)
            if (
                expected_clock is not None
                and output_clock is not None
                and output_clock != expected_clock
            ):
                raise _InvalidNodeOutput(
                    f"Output {port_id!r} uses clock {output_clock}, expected {expected_clock}"
                )
