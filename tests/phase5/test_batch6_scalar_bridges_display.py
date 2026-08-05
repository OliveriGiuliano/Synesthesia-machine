"""Algorithm, metadata, and preview tests for Phase 5 Batch 6."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    FrameContext,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import CachePolicy, ExecutionKind, ExpectedNodeError, NodeDefinition
from synesthesia_machine.nodes.utility import (
    create_scalar_bridge_definitions,
    create_utility_registry,
)
from synesthesia_machine.nodes.visualization import CHANNEL_DISPLAY_TYPE_ID
from synesthesia_machine.runtime import ExecutionPlan, PortKey, PreviewBroker, TickResult
from tests.phase5.conformance import assert_scheduler_propagates_no_data

NODE_ID = UUID("00000000-0000-0000-0000-000000006060")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000006061")
DISPLAY_ID = UUID("00000000-0000-0000-0000-000000006062")
BATCH6_IDS = (
    "synmachine.utility.channel_statistics",
    "synmachine.utility.remap_number",
    "synmachine.utility.float_to_integer",
    "synmachine.visualization.channel_display",
)
PHASE5_UTILITY_IDS = {
    "synmachine.utility.channel_statistics",
    "synmachine.utility.compare",
    "synmachine.utility.conditional",
    "synmachine.utility.float_to_integer",
    "synmachine.utility.logic",
    "synmachine.utility.math",
    "synmachine.utility.number",
    "synmachine.utility.pass_through",
    "synmachine.utility.remap_number",
}
STATISTICS = ("MEAN", "MEDIAN", "MINIMUM", "MAXIMUM", "STANDARD_DEVIATION", "PERCENTILE")
CONVERSION_MODES = ("ROUND", "FLOOR", "CEIL", "TRUNCATE")


def _definition(type_id: str) -> NodeDefinition:
    return create_application_registry().require(type_id)


def _parameters(
    definition: NodeDefinition,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, ParameterValue]:
    parameters, errors = definition.parameter_values(overrides or {})
    assert not errors
    return parameters


def _process(
    type_id: str,
    inputs: Mapping[str, RuntimeValue],
    overrides: Mapping[str, object] | None = None,
) -> RuntimeValue:
    definition = _definition(type_id)
    output = definition.runtime_factory(NODE_ID).process(
        inputs,
        _parameters(definition, overrides),
        _context(1),
    )
    return output["value"]


def _context(tick_index: int) -> FrameContext:
    return FrameContext(SOURCE_ID, tick_index, tick_index - 1, 0.0, 1, None, False)


def _channel(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    *,
    nominal_minimum: float = 0.0,
    nominal_maximum: float = 1.0,
    tick_index: int = 1,
) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(data),
        ChannelSemantic.LUMINANCE,
        nominal_minimum,
        nominal_maximum,
        False,
        _context(tick_index),
    )


def _channel_display_plan(
    *, preview_fps: int = 30, max_dimension: int = 800
) -> tuple[ExecutionPlan, PortKey]:
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={"file_path": "unused.mp4"},
    )
    luminance_id = document.add_node("synmachine.image.to_luminance")
    document.add_node(
        CHANNEL_DISPLAY_TYPE_ID,
        node_id=DISPLAY_ID,
        parameters={"preview_fps": preview_fps, "max_dimension": max_dimension},
    )
    document.add_connection(SOURCE_ID, "image", luminance_id, "image")
    document.add_connection(luminance_id, "channel", DISPLAY_ID, "channel")
    result = GraphCompiler(create_application_registry()).compile(document.snapshot())
    assert result.plan is not None
    return result.plan, PortKey(luminance_id, "channel")


def test_batch6_metadata_has_exact_order_ports_defaults_and_policies() -> None:
    scalar_definitions = create_scalar_bridge_definitions()
    visualization = _definition(CHANNEL_DISPLAY_TYPE_ID)
    definitions = (*scalar_definitions, visualization)
    assert tuple(definition.type_id for definition in definitions) == BATCH6_IDS

    statistics, remap, conversion, channel_display = definitions
    assert tuple(port.id for port in statistics.inputs) == ("channel",)
    assert tuple(port.id for port in statistics.outputs) == ("value",)
    assert tuple(parameter.id for parameter in statistics.parameters) == (
        "statistic",
        "percentile",
        "ignore_non_finite",
    )
    assert tuple(parameter.default for parameter in statistics.parameters) == ("MEAN", 50.0, True)
    assert statistics.parameters[0].choices == STATISTICS
    assert statistics.parameters[1].minimum == 0.0
    assert statistics.parameters[1].maximum == 100.0

    assert tuple(port.id for port in remap.inputs) == ("value",)
    assert tuple(port.id for port in remap.outputs) == ("value",)
    assert tuple(parameter.id for parameter in remap.parameters) == (
        "input_minimum",
        "input_maximum",
        "output_minimum",
        "output_maximum",
        "clamp",
    )
    assert tuple(parameter.default for parameter in remap.parameters) == (0.0, 1.0, 0.0, 1.0, False)
    assert all(parameter.connectable for parameter in remap.parameters[:4])
    assert all(
        parameter.connected_port_type is PortType.FLOAT for parameter in remap.parameters[:4]
    )

    assert tuple(port.id for port in conversion.inputs) == ("value",)
    assert tuple(port.id for port in conversion.outputs) == ("value",)
    assert conversion.outputs[0].value_type is PortType.INT
    assert conversion.parameters[0].id == "mode"
    assert conversion.parameters[0].default == "ROUND"
    assert conversion.parameters[0].choices == CONVERSION_MODES

    assert channel_display.execution_kind is ExecutionKind.VISUALIZER
    assert channel_display.cache_policy is CachePolicy.NEVER
    assert tuple(port.id for port in channel_display.inputs) == ("channel",)
    assert not channel_display.outputs
    assert tuple(parameter.id for parameter in channel_display.parameters) == (
        "preview_fps",
        "max_dimension",
        "fit_mode",
        "value_display_mode",
        "show_histogram",
    )
    assert tuple(parameter.default for parameter in channel_display.parameters) == (
        30,
        800,
        "CONTAIN",
        "NOMINAL_RANGE",
        False,
    )


@pytest.mark.parametrize("type_id", BATCH6_IDS)
def test_batch6_definitions_are_registered(type_id: str) -> None:
    assert create_application_registry().require(type_id).type_id == type_id


@pytest.mark.parametrize("definition", create_scalar_bridge_definitions())
def test_scheduler_propagates_no_data_for_scalar_bridges(definition: NodeDefinition) -> None:
    assert_scheduler_propagates_no_data(definition, {})


@pytest.mark.parametrize(
    ("statistic", "expected"),
    (
        ("MEAN", 2.5),
        ("MEDIAN", 2.5),
        ("MINIMUM", 1.0),
        ("MAXIMUM", 4.0),
        ("STANDARD_DEVIATION", np.std(np.array([1.0, 2.0, 3.0, 4.0]))),
        ("PERCENTILE", 3.25),
    ),
)
def test_channel_statistics_algorithms(statistic: str, expected: float) -> None:
    channel = _channel(np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32))
    before = channel.data.copy()
    value = _process(
        "synmachine.utility.channel_statistics",
        {"channel": channel},
        {"statistic": statistic, "percentile": 75.0},
    )
    assert isinstance(value, float)
    assert value == pytest.approx(expected)
    assert np.array_equal(channel.data, before)
    assert not channel.data.flags.writeable


def test_channel_statistics_finite_policy_and_empty_selection_are_explicit() -> None:
    mixed = _channel(np.array([[1.0, np.nan], [np.inf, 3.0]], dtype=np.float32))
    assert _process("synmachine.utility.channel_statistics", {"channel": mixed}) == pytest.approx(
        2.0
    )
    propagated = _process(
        "synmachine.utility.channel_statistics",
        {"channel": mixed},
        {"ignore_non_finite": False},
    )
    assert isinstance(propagated, float) and np.isnan(propagated)

    empty = _channel(np.array([[np.nan, np.inf]], dtype=np.float32))
    with pytest.raises(ExpectedNodeError, match="no finite samples") as captured:
        _process("synmachine.utility.channel_statistics", {"channel": empty})
    assert captured.value.code == "invalid_channel_statistics"


def test_channel_statistics_percentile_validation_rejects_non_finite() -> None:
    definition = _definition("synmachine.utility.channel_statistics")
    for value in (float("nan"), float("inf"), float("-inf")):
        _, errors = definition.parameter_values({"percentile": value})
        assert errors and "percentile" in errors[0]


def test_remap_number_supports_reversed_ranges_clamping_and_connected_parameters() -> None:
    assert _process(
        "synmachine.utility.remap_number",
        {"value": 0.25},
        {"output_minimum": 10.0, "output_maximum": 20.0},
    ) == pytest.approx(12.5)
    assert _process(
        "synmachine.utility.remap_number",
        {"value": 0.25},
        {"input_minimum": 1.0, "input_maximum": 0.0},
    ) == pytest.approx(0.75)
    assert _process(
        "synmachine.utility.remap_number",
        {"value": 2.0},
        {"clamp": True},
    ) == pytest.approx(1.0)
    assert _process(
        "synmachine.utility.remap_number",
        {
            "value": 5.0,
            "input_minimum": 0.0,
            "input_maximum": 10.0,
            "output_minimum": -1.0,
            "output_maximum": 1.0,
        },
        {"input_maximum": 1.0, "output_minimum": 20.0},
    ) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("value", "clamp", "expected_kind"),
    (
        (float("nan"), False, "NAN"),
        (float("inf"), False, "POSITIVE_INFINITY"),
        (float("-inf"), False, "NEGATIVE_INFINITY"),
        (float("inf"), True, "ONE"),
        (float("-inf"), True, "ZERO"),
    ),
)
def test_remap_number_ieee_non_finite_policy(
    value: float,
    clamp: bool,
    expected_kind: str,
) -> None:
    result = _process(
        "synmachine.utility.remap_number",
        {"value": value},
        {"clamp": clamp},
    )
    assert isinstance(result, float)
    if expected_kind == "NAN":
        assert np.isnan(result)
    elif expected_kind == "POSITIVE_INFINITY":
        assert np.isposinf(result)
    elif expected_kind == "NEGATIVE_INFINITY":
        assert np.isneginf(result)
    elif expected_kind == "ONE":
        assert result == 1.0
    else:
        assert result == 0.0


def test_remap_number_rejects_equal_literal_and_connected_input_endpoints() -> None:
    definition = _definition("synmachine.utility.remap_number")
    _, errors = definition.parameter_values({"input_minimum": 2.0, "input_maximum": 2.0})
    assert errors and "input endpoints" in errors[0]
    with pytest.raises(ExpectedNodeError, match="must not be equal") as captured:
        _process(
            "synmachine.utility.remap_number",
            {"value": 1.0, "input_minimum": 3.0, "input_maximum": 3.0},
        )
    assert captured.value.code == "invalid_remap_number"


@pytest.mark.parametrize(
    ("mode", "expected_positive", "expected_negative"),
    (
        ("ROUND", 2, -2),
        ("FLOOR", 1, -2),
        ("CEIL", 2, -1),
        ("TRUNCATE", 1, -1),
    ),
)
def test_float_to_integer_modes(
    mode: str,
    expected_positive: int,
    expected_negative: int,
) -> None:
    positive = _process("synmachine.utility.float_to_integer", {"value": 1.5}, {"mode": mode})
    negative = _process("synmachine.utility.float_to_integer", {"value": -1.5}, {"mode": mode})
    assert positive == expected_positive and type(positive) is int
    assert negative == expected_negative and type(negative) is int


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_float_to_integer_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ExpectedNodeError, match="finite input") as captured:
        _process("synmachine.utility.float_to_integer", {"value": value})
    assert captured.value.code == "finite_required"


def test_channel_display_sanitizes_nominal_range_and_publishes_bounded_uint8() -> None:
    plan, source = _channel_display_plan(max_dimension=64)
    channel = _channel(
        np.array(
            [
                [-1.0, 0.0, 0.5, 1.0, 2.0, np.nan, np.inf, -np.inf],
                [0.25, 0.75, 0.5, 0.0, 1.0, 0.5, 0.5, 0.5],
            ],
            dtype=np.float32,
        ),
        nominal_minimum=0.0,
        nominal_maximum=1.0,
        tick_index=7,
    )
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(TickResult({source: channel}, (), {}))

    preview = broker.poll_images()[0]
    assert preview.node_id == DISPLAY_ID
    assert preview.tick_index == 7
    assert (preview.width, preview.height, preview.channels) == (8, 2, 3)
    assert preview.data.dtype == np.uint8
    assert preview.data.flags.c_contiguous and not preview.data.flags.writeable
    assert np.array_equal(preview.data[..., 0], preview.data[..., 1])
    assert np.array_equal(preview.data[..., 1], preview.data[..., 2])
    assert tuple(preview.data[0, :, 0]) == (0, 0, 128, 255, 255, 0, 255, 0)
    assert not channel.data.flags.writeable


def test_channel_display_resizes_and_throttles_without_publishing_float32() -> None:
    plan, source = _channel_display_plan(preview_fps=30, max_dimension=64)
    now = [0.0]
    broker = PreviewBroker(monotonic=lambda: now[0])
    broker.configure(plan)
    channel = _channel(np.ones((50, 100), dtype=np.float32), tick_index=1)
    broker.publish(TickResult({source: channel}, (), {}))
    first = broker.poll_images()[0]
    assert (first.width, first.height, first.channels) == (64, 32, 3)
    assert first.data.dtype == np.uint8 and first.data.nbytes == 64 * 32 * 3

    now[0] = 1.0 / 60.0
    broker.publish(TickResult({source: _channel(channel.data, tick_index=2)}, (), {}))
    assert broker.poll_images()[0].tick_index == 1
    now[0] = 1.0 / 30.0
    broker.publish(TickResult({source: _channel(channel.data, tick_index=3)}, (), {}))
    assert broker.poll_images({DISPLAY_ID: 1})[0].tick_index == 3


def test_channel_display_is_a_default_demand_root() -> None:
    plan, _ = _channel_display_plan()
    assert DISPLAY_ID in plan.demand_roots
    assert next(node for node in plan.nodes if node.node_id == DISPLAY_ID).is_demanded


def test_batch6_utility_entry_point_remains_phase1_compatible() -> None:
    registry = create_utility_registry()
    assert registry.require("synmachine.utility.number").type_id == "synmachine.utility.number"
    assert registry.require("synmachine.utility.channel_statistics").type_id == BATCH6_IDS[0]
    current_ids = {definition.type_id for definition in registry.definitions()}
    assert len(PHASE5_UTILITY_IDS) == 9
    assert current_ids >= PHASE5_UTILITY_IDS


def test_channel_display_ignores_nodata_publications() -> None:
    plan, source = _channel_display_plan()
    broker = PreviewBroker()
    broker.configure(plan)
    broker.publish(TickResult({source: NoData}, (), {}))
    assert broker.poll_images() == ()
