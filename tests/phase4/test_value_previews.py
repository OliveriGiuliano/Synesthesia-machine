"""Focused tests for the scalar value-preview path in the preview broker.

The value preview advertises every connected INT/FLOAT output of every demanded
producer so the UI can render a compact live number on each scalar connection. Each
preview is keyed by (producer, source port), so distinct scalar outputs on the same
node never overwrite one another. Target selection, scalar formatting, tick-index
resolution, and the polling/threshold behavior all live in ``runtime/previews.py``.

Like image and channel previews, value previews are anchored on the producing node
rather than a downstream display node, so a scalar pill can show live data regardless
of its destination. The UI forces the producer of a visible scalar pill to be a demand
root (see ``_pill_producer_roots``), which is modeled here by a demanded producer whose
output feeds a consumer that may itself be undemanded. These tests hand-build a minimal
``ExecutionPlan`` to exercise the broker's value-preview behavior directly, independent
of the compiler.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    FrameContext,
    NoData,
    ParameterValue,
    PortType,
    ResetReason,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.nodes import ExecutionKind, NodeDefinition, OutputPortSpec
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    PreviewBroker,
    TickResult,
)

SOURCE_ID = UUID("00000000-0000-0000-0000-0000000060a0")
SCALAR_PRODUCER_ID = UUID("00000000-0000-0000-0000-0000000060a1")
SCALAR_CONSUMER_ID = UUID("00000000-0000-0000-0000-0000000060a2")
SCALAR_CONSUMER_B_ID = UUID("00000000-0000-0000-0000-0000000060a4")
VALUE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-0000000060a3")


def _definition(type_id: str) -> NodeDefinition:
    return create_application_registry().require(type_id)


def _context(tick_index: int) -> FrameContext:
    return FrameContext(SOURCE_ID, tick_index, tick_index - 1, 0.0, 1, None, False)


def _channel(tick_index: int) -> ChannelFrame:
    """A throwaway frame whose only role is to carry a tick index for the broker."""

    return ChannelFrame(
        read_only_float32(np.ones((2, 2), dtype=np.float32)),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        _context(tick_index),
    )


def _value_preview_plan(
    *,
    producer_type_id: str = "synmachine.utility.number",
    producer_demanded: bool = True,
    consumer_demanded: bool = True,
    consumed: bool = True,
) -> tuple[ExecutionPlan, PortKey]:
    """Build a plan with a scalar producer whose ``value`` output may feed a consumer.

    ``number`` exposes a FLOAT ``value`` output and ``float_to_integer`` exposes an INT
    one; the producer's declared output type is read from the real definition so the
    target's ``port_type`` reflects the node it belongs to.
    """

    producer_definition = _definition(producer_type_id)
    producer_output_type = producer_definition.outputs[0].value_type
    producer = CompiledNode(
        node_id=SCALAR_PRODUCER_ID,
        definition=producer_definition,
        output_types={"value": producer_output_type},
        is_demanded=producer_demanded,
    )
    input_bindings = (
        {"value": InputBinding(source=PortKey(SCALAR_PRODUCER_ID, "value"))} if consumed else {}
    )
    consumer = CompiledNode(
        node_id=SCALAR_CONSUMER_ID,
        definition=_definition("synmachine.utility.remap_number"),
        input_bindings=input_bindings,
        output_types={"value": PortType.FLOAT},
        is_demanded=consumer_demanded,
    )
    plan = ExecutionPlan(
        document_id=VALUE_DOCUMENT_ID,
        graph_revision=1,
        nodes=(producer, consumer),
        demand_roots=frozenset({SCALAR_CONSUMER_ID}),
    )
    return plan, PortKey(SCALAR_PRODUCER_ID, "value")


def _value_tick_result(
    scalar_source: PortKey,
    *,
    scalar: object,
    tick_index: int,
    include_frame: bool = True,
) -> TickResult:
    values: dict[PortKey, object] = {}
    if include_frame:
        values[PortKey(SOURCE_ID, "frame")] = _channel(tick_index)
    values[scalar_source] = scalar
    return TickResult(values, (), {})


class _ScalarStubRuntime:
    """Satisfies the runtime factory contract; the preview broker never invokes it."""

    def __init__(self, node_id: UUID) -> None:
        del node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        pass


def _two_scalar_output_definition() -> NodeDefinition:
    def factory(node_id: UUID) -> _ScalarStubRuntime:
        return _ScalarStubRuntime(node_id)

    return NodeDefinition(
        type_id="synmachine.test.two_scalar_outputs",
        implementation_version=1,
        display_name="Two Scalar Outputs",
        category="Test",
        description="Two connected INT/FLOAT outputs for value-preview routing tests.",
        inputs=(),
        outputs=(
            OutputPortSpec("value_a", "Value A", PortType.FLOAT),
            OutputPortSpec("value_b", "Value B", PortType.INT),
        ),
        parameters=(),
        execution_kind=ExecutionKind.STATELESS,
        runtime_factory=factory,
    )


def _two_port_value_plan() -> ExecutionPlan:
    """A single demanded producer exposing two connected scalar outputs, one per port."""

    producer = CompiledNode(
        node_id=SCALAR_PRODUCER_ID,
        definition=_two_scalar_output_definition(),
        output_types={"value_a": PortType.FLOAT, "value_b": PortType.INT},
        is_demanded=True,
    )
    consumer_a = CompiledNode(
        node_id=SCALAR_CONSUMER_ID,
        definition=_definition("synmachine.utility.remap_number"),
        input_bindings={"value": InputBinding(source=PortKey(SCALAR_PRODUCER_ID, "value_a"))},
        output_types={"value": PortType.FLOAT},
        is_demanded=True,
    )
    consumer_b = CompiledNode(
        node_id=SCALAR_CONSUMER_B_ID,
        definition=_definition("synmachine.utility.remap_number"),
        input_bindings={"value": InputBinding(source=PortKey(SCALAR_PRODUCER_ID, "value_b"))},
        output_types={"value": PortType.FLOAT},
        is_demanded=True,
    )
    return ExecutionPlan(
        document_id=VALUE_DOCUMENT_ID,
        graph_revision=1,
        nodes=(producer, consumer_a, consumer_b),
        demand_roots=frozenset({SCALAR_CONSUMER_ID, SCALAR_CONSUMER_B_ID}),
    )


def test_value_preview_publishes_consumed_scalar_with_tick_index() -> None:
    plan, scalar_source = _value_preview_plan()
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=3.14, tick_index=7))

    (preview,) = broker.poll_values()
    assert preview.owner_id == SCALAR_PRODUCER_ID
    assert preview.sequence == 1
    assert preview.tick_index == 7
    assert preview.port_type == "FLOAT"
    assert preview.text == "3.14"


def test_value_preview_formats_integers_exactly() -> None:
    plan, scalar_source = _value_preview_plan(
        producer_type_id="synmachine.utility.float_to_integer"
    )
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=42, tick_index=3))

    (preview,) = broker.poll_values()
    assert preview.port_type == "INT"
    assert preview.text == "42"


def test_value_preview_formats_numpy_scalars() -> None:
    """NumPy integer/float scalars (e.g. np.int64, np.float32) are accepted and formatted,
    not silently skipped, even though they are not Python int/float subclasses."""

    int_plan, int_source = _value_preview_plan(
        producer_type_id="synmachine.utility.float_to_integer"
    )
    int_broker = PreviewBroker()
    int_broker.configure(int_plan)
    int_broker.publish(_value_tick_result(int_source, scalar=np.int64(42), tick_index=3))
    (int_preview,) = int_broker.poll_values()
    assert int_preview.port_type == "INT"
    assert int_preview.text == "42"

    float_plan, float_source = _value_preview_plan()
    float_broker = PreviewBroker()
    float_broker.configure(float_plan)
    float_broker.publish(_value_tick_result(float_source, scalar=np.float32(2.5), tick_index=4))
    (float_preview,) = float_broker.poll_values()
    assert float_preview.port_type == "FLOAT"
    assert float_preview.text == "2.5"


def test_value_preview_requires_a_frame_tick_index() -> None:
    plan, scalar_source = _value_preview_plan()
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(
        _value_tick_result(scalar_source, scalar=3.14, tick_index=7, include_frame=False)
    )

    assert broker.poll_values() == ()


def test_value_preview_skips_non_scalar_publications() -> None:
    plan, scalar_source = _value_preview_plan()
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=NoData, tick_index=7))

    assert broker.poll_values() == ()


def test_value_preview_requires_a_connected_output() -> None:
    plan, scalar_source = _value_preview_plan(consumed=False)
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=3.14, tick_index=7))

    assert broker.poll_values() == ()


def test_value_preview_anchors_on_producer_regardless_of_consumer_demand() -> None:
    """A demanded producer whose scalar output feeds an undemanded consumer still gets a
    value target, mirroring image/channel pills: the pill is anchored on the producer."""

    plan, scalar_source = _value_preview_plan(consumer_demanded=False)
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=3.14, tick_index=7))

    (preview,) = broker.poll_values()
    assert preview.owner_id == SCALAR_PRODUCER_ID
    assert preview.text == "3.14"


def test_value_preview_requires_a_demanded_producer() -> None:
    plan, scalar_source = _value_preview_plan(producer_demanded=False)
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=3.14, tick_index=7))

    assert broker.poll_values() == ()


def test_value_preview_keeps_latest_and_respects_sequence_thresholds() -> None:
    plan, scalar_source = _value_preview_plan()
    now = [0.0]
    broker = PreviewBroker(monotonic=lambda: now[0])
    broker.configure(plan)

    broker.publish(_value_tick_result(scalar_source, scalar=1.0, tick_index=1))
    (latest,) = broker.poll_values()
    assert (latest.text, latest.tick_index, latest.sequence) == ("1", 1, 1)
    # Nothing newer than the acknowledged sequence.
    assert broker.poll_values({(SCALAR_PRODUCER_ID, "value"): latest.sequence}) == ()

    # A later due tick coalesces onto the same (owner, port), replacing the latest value.
    now[0] = 1.0 / 30.0
    broker.publish(_value_tick_result(scalar_source, scalar=2.5, tick_index=2))
    (newer,) = broker.poll_values({(SCALAR_PRODUCER_ID, "value"): latest.sequence})
    assert (newer.text, newer.tick_index, newer.sequence) == ("2.5", 2, 2)
    # A second poll at the same threshold finds nothing newer than the coalesced value.
    assert broker.poll_values({(SCALAR_PRODUCER_ID, "value"): newer.sequence}) == ()


def test_value_preview_target_is_cleared_on_reconfigure() -> None:
    plan, scalar_source = _value_preview_plan()
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(_value_tick_result(scalar_source, scalar=3.14, tick_index=7))
    assert len(broker.poll_values()) == 1

    # A new activation with a plan that has no value target must drop the old preview.
    empty_plan, _ = _value_preview_plan(consumed=False)
    broker.configure(empty_plan)
    broker.publish(_value_tick_result(scalar_source, scalar=9.9, tick_index=8))

    assert broker.poll_values() == ()


def test_value_preview_routes_each_connected_output_separately() -> None:
    """Two connected scalar outputs on one producer each get their own (owner, port)
    preview instead of sharing a single pill value."""

    plan = _two_port_value_plan()
    now = [0.0]
    broker = PreviewBroker(monotonic=lambda: now[0])
    broker.configure(plan)

    broker.publish(
        _value_tick_result(PortKey(SCALAR_PRODUCER_ID, "value_a"), scalar=3.5, tick_index=7)
    )
    (first,) = broker.poll_values()
    assert (first.source_port_id, first.port_type, first.text) == ("value_a", "FLOAT", "3.5")

    # Publishing value_b on the same producer must not overwrite the value_a preview.
    now[0] = 1.0 / 30.0
    broker.publish(
        TickResult(
            {
                PortKey(SOURCE_ID, "frame"): _channel(8),
                PortKey(SCALAR_PRODUCER_ID, "value_b"): 42,
            },
            (),
            {},
        )
    )

    previews = {preview.source_port_id: preview for preview in broker.poll_values()}
    assert set(previews) == {"value_a", "value_b"}
    assert previews["value_a"].text == "3.5"
    assert (previews["value_b"].text, previews["value_b"].port_type) == ("42", "INT")
    assert previews["value_b"].source_port_id == "value_b"


def test_value_preview_sanitizes_non_finite_scalars() -> None:
    """inf/nan float scalars render as a neutral placeholder, never raw text."""

    plan, scalar_source = _value_preview_plan()
    now = [0.0]
    broker = PreviewBroker(monotonic=lambda: now[0])
    broker.configure(plan)

    broker.publish(_value_tick_result(scalar_source, scalar=float("inf"), tick_index=3))
    (inf_preview,) = broker.poll_values()
    assert inf_preview.text == "\u2014"

    now[0] = 1.0 / 30.0
    broker.publish(_value_tick_result(scalar_source, scalar=float("nan"), tick_index=4))
    (nan_preview,) = broker.poll_values({(SCALAR_PRODUCER_ID, "value"): inf_preview.sequence})
    assert nan_preview.text == "\u2014"
