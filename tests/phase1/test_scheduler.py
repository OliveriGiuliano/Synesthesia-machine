"""Scheduler, utility node, cache, and EngineFacade tests."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import cast
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import (
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    NodeRegistry,
    OutputPortSpec,
)
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.runtime import EngineFacade, PortKey, Scheduler
from tests.phase1.helpers import ProbeRuntime, RuntimeCounters, frame_context, make_definition

CLOCK_ID = UUID("00000000-0000-0000-0000-000000000100")
NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")
NODE_C = UUID("00000000-0000-0000-0000-00000000000c")
NODE_D = UUID("00000000-0000-0000-0000-00000000000d")


def test_fan_out_executes_upstream_once_per_tick_and_propagates_no_data() -> None:
    counters: dict[UUID, RuntimeCounters] = {}
    registry = NodeRegistry(
        (
            make_definition(
                "test.source",
                execution_kind=ExecutionKind.SOURCE,
                counters=counters,
            ),
            make_definition("test.sink", input_type=PortType.FLOAT, counters=counters),
        )
    )
    document = GraphDocument()
    source = document.add_node("test.source", node_id=NODE_A)
    intermediate = document.add_node("test.sink", node_id=NODE_B)
    first = document.add_node("test.sink", node_id=NODE_C)
    second = document.add_node("test.sink", node_id=NODE_D)
    document.add_connection(source, "value", intermediate, "value")
    document.add_connection(intermediate, "value", first, "value")
    document.add_connection(intermediate, "value", second, "value")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    scheduler = Scheduler(plan)
    result = scheduler.execute_tick(
        frame_context(clock_id=source), source_values={PortKey(source, "value"): 3.0}
    )
    assert result.invocation_counts == {intermediate: 1, first: 1, second: 1}
    assert result.values[PortKey(first, "value")] == 3.0
    assert result.values[PortKey(second, "value")] == 3.0

    missing_result = scheduler.execute_tick(
        frame_context(clock_id=source, tick_index=2),
        source_values={PortKey(source, "value"): NoData},
    )
    assert missing_result.values[PortKey(first, "value")] is NoData
    assert intermediate not in missing_result.invocation_counts
    assert first not in missing_result.invocation_counts


def test_node_that_handles_no_data_is_invoked() -> None:
    counters: dict[UUID, RuntimeCounters] = {}
    registry = NodeRegistry(
        (
            make_definition("test.source", execution_kind=ExecutionKind.SOURCE),
            make_definition(
                "test.handler",
                input_type=PortType.FLOAT,
                handles_no_data=True,
                counters=counters,
            ),
        )
    )
    document = GraphDocument()
    source = document.add_node("test.source", node_id=NODE_A)
    handler = document.add_node("test.handler", node_id=NODE_B)
    document.add_connection(source, "value", handler, "value")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None

    result = Scheduler(plan).execute_tick(
        frame_context(clock_id=source),
        source_values={PortKey(source, "value"): NoData},
    )

    assert result.invocation_counts == {handler: 1}
    assert result.values[PortKey(handler, "value")] is NoData


def test_static_cache_survives_ticks_and_new_plan_invalidates_it() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    number = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "FLOAT", "float_value": 2.0},
    )
    passthrough = document.add_node("synmachine.utility.pass_through", node_id=NODE_B)
    document.add_connection(number, "value", passthrough, "value")
    facade = EngineFacade(registry)
    assert facade.activate(document.snapshot()).plan is not None
    first = facade.tick(frame_context(clock_id=CLOCK_ID))
    second = facade.tick(frame_context(clock_id=CLOCK_ID, tick_index=2))
    assert first.values[PortKey(passthrough, "value")] == 2.0
    assert second.invocation_counts == {}

    document.set_parameter(number, "float_value", 5.0)
    assert facade.activate(document.snapshot()).plan is not None
    changed = facade.tick(frame_context(clock_id=CLOCK_ID, tick_index=3))
    assert changed.values[PortKey(passthrough, "value")] == 5.0
    assert changed.invocation_counts == {number: 1, passthrough: 1}


def test_connection_change_invalidates_static_cache() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    first_number = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "FLOAT", "float_value": 2.0},
    )
    second_number = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 8.0},
    )
    passthrough = document.add_node("synmachine.utility.pass_through", node_id=NODE_C)
    document.add_connection(first_number, "value", passthrough, "value")
    facade = EngineFacade(registry)
    assert facade.activate(document.snapshot()).plan is not None
    assert (
        facade.tick(frame_context(clock_id=CLOCK_ID)).values[PortKey(passthrough, "value")] == 2.0
    )

    document.add_connection(second_number, "value", passthrough, "value")
    assert facade.activate(document.snapshot()).plan is not None
    changed = facade.tick(frame_context(clock_id=CLOCK_ID, tick_index=2))
    assert changed.values[PortKey(passthrough, "value")] == 8.0
    assert passthrough in changed.invocation_counts


def test_expected_error_becomes_no_data_without_terminating_tick() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    numerator = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "FLOAT", "float_value": 1.0},
    )
    denominator = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 0.0},
    )
    divide = document.add_node(
        "synmachine.utility.math", node_id=NODE_C, parameters={"operation": "DIVIDE"}
    )
    downstream = document.add_node("synmachine.utility.pass_through", node_id=NODE_D)
    document.add_connection(numerator, "value", divide, "a")
    document.add_connection(denominator, "value", divide, "b")
    document.add_connection(divide, "value", downstream, "value")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    result = Scheduler(plan).execute_tick(frame_context(clock_id=CLOCK_ID))
    assert result.errors[0].code == "math_domain"
    assert result.values[PortKey(divide, "value")] is NoData
    assert result.values[PortKey(downstream, "value")] is NoData
    assert downstream not in result.invocation_counts


def test_outputless_demanded_sink_executes_once_per_tick() -> None:
    counters: dict[UUID, RuntimeCounters] = {}

    def sink_factory(node_id: UUID) -> ProbeRuntime:
        return ProbeRuntime(node_id, counters)

    sink_definition = NodeDefinition(
        "test.outputless_sink",
        1,
        "Outputless Sink",
        "Test",
        "Consumes a value without publishing outputs.",
        (InputPortSpec("value", "Value", PortType.FLOAT),),
        (),
        (),
        ExecutionKind.SINK,
        sink_factory,
    )
    registry = NodeRegistry((make_definition("test.source"), sink_definition))
    document = GraphDocument()
    source = document.add_node("test.source", node_id=NODE_A)
    sink = document.add_node("test.outputless_sink", node_id=NODE_B)
    document.add_connection(source, "value", sink, "value")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None

    result = Scheduler(plan).execute_tick(
        frame_context(clock_id=CLOCK_ID), source_values={PortKey(source, "value"): 1.0}
    )

    assert result.invocation_counts == {sink: 1}
    assert counters[sink].process == 1


def test_power_domain_error_stays_within_float_runtime_contract() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    negative = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "FLOAT", "float_value": -1.0},
    )
    exponent = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 0.5},
    )
    power = document.add_node(
        "synmachine.utility.math", node_id=NODE_C, parameters={"operation": "POWER"}
    )
    document.add_connection(negative, "value", power, "a")
    document.add_connection(exponent, "value", power, "b")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None

    result = Scheduler(plan).execute_tick(frame_context(clock_id=CLOCK_ID))

    assert result.errors[0].code == "math_domain"
    assert result.values[PortKey(power, "value")] is NoData


def test_utility_graph_is_deterministic_across_synthetic_ticks() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    left = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "INT", "int_value": 2},
    )
    right = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 3.5},
    )
    add = document.add_node(
        "synmachine.utility.math", node_id=NODE_C, parameters={"operation": "ADD"}
    )
    document.add_connection(left, "value", add, "a")
    document.add_connection(right, "value", add, "b")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    scheduler = Scheduler(plan)
    results = [
        scheduler.execute_tick(frame_context(clock_id=CLOCK_ID, tick_index=tick))
        for tick in range(1, 4)
    ]
    assert [result.values[PortKey(add, "value")] for result in results] == [5.5, 5.5, 5.5]


def test_compare_logic_and_conditional_nodes_execute_together() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    high = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_A,
        parameters={"number_type": "FLOAT", "float_value": 5.0},
    )
    low = document.add_node(
        "synmachine.utility.number",
        node_id=NODE_B,
        parameters={"number_type": "FLOAT", "float_value": 3.0},
    )
    greater = document.add_node(
        "synmachine.utility.compare", node_id=NODE_C, parameters={"operation": "GT"}
    )
    less = document.add_node(
        "synmachine.utility.compare", node_id=NODE_D, parameters={"operation": "LT"}
    )
    logic_id = UUID("00000000-0000-0000-0000-00000000000e")
    conditional_id = UUID("00000000-0000-0000-0000-00000000000f")
    logic = document.add_node(
        "synmachine.utility.logic", node_id=logic_id, parameters={"operation": "XOR"}
    )
    conditional = document.add_node("synmachine.utility.conditional", node_id=conditional_id)
    for comparison in (greater, less):
        document.add_connection(high, "value", comparison, "a")
        document.add_connection(low, "value", comparison, "b")
    document.add_connection(greater, "value", logic, "a")
    document.add_connection(less, "value", logic, "b")
    document.add_connection(logic, "value", conditional, "condition")
    document.add_connection(high, "value", conditional, "if_true")
    document.add_connection(low, "value", conditional, "if_false")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None

    result = Scheduler(plan).execute_tick(frame_context(clock_id=CLOCK_ID))

    assert result.values[PortKey(greater, "value")] is True
    assert result.values[PortKey(less, "value")] is False
    assert result.values[PortKey(logic, "value")] is True
    assert result.values[PortKey(conditional, "value")] == 5.0


def test_engine_facade_keeps_old_plan_when_recompile_fails() -> None:
    facade = EngineFacade(create_utility_registry())
    valid = GraphDocument()
    valid.add_node("synmachine.utility.number", node_id=NODE_A)
    activation = facade.activate(valid.snapshot())
    assert activation.plan is not None
    old_plan = facade.active_plan

    invalid = GraphDocument()
    invalid.add_node("unknown.node", node_id=NODE_B)
    failed = facade.activate(invalid.snapshot())
    assert failed.plan is None
    assert facade.active_plan is old_plan


def test_timing_hook_and_immutable_inputs_are_exposed_at_runtime_boundary() -> None:
    counters: dict[UUID, RuntimeCounters] = {}

    class MutatingRuntime(ProbeRuntime):
        def process(
            self,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, ParameterValue],
            context: FrameContext,
        ) -> Mapping[str, RuntimeValue]:
            mutable_inputs = cast(MutableMapping[str, RuntimeValue], inputs)
            mutable_inputs["value"] = 2.0
            return super().process(inputs, parameters, context)

    def factory(node_id: UUID) -> MutatingRuntime:
        return MutatingRuntime(node_id, counters)

    definition = NodeDefinition(
        "test.mutating",
        1,
        "Mutating",
        "Test",
        "Attempts to mutate input mapping.",
        (InputPortSpec("value", "Value", PortType.FLOAT),),
        (OutputPortSpec("value", "Value", PortType.FLOAT),),
        (),
        ExecutionKind.STATELESS,
        factory,
    )
    registry = NodeRegistry((make_definition("test.source"), definition))
    document = GraphDocument()
    source = document.add_node("test.source", node_id=NODE_A)
    mutating = document.add_node("test.mutating", node_id=NODE_B)
    document.add_connection(source, "value", mutating, "value")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    timings: list[tuple[UUID, int]] = []
    result = Scheduler(
        plan, timing_hook=lambda node_id, ns: timings.append((node_id, ns))
    ).execute_tick(frame_context(clock_id=CLOCK_ID), source_values={PortKey(source, "value"): 1.0})
    assert result.errors[0].code == "unexpected_node_error"
    assert result.values[PortKey(mutating, "value")] is NoData
    assert timings[0][0] == mutating and timings[0][1] >= 0
