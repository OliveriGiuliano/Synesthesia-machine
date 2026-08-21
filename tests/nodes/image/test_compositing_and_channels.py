"""Image compositing, channel metadata, and compiler tests."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest
from tests.support.image_conformance import assert_scheduler_propagates_no_data

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.graph.compiler import GraphCompiler
from synesthesia_machine.media import color_space_descriptor, convert_image, image_to_luminance
from synesthesia_machine.nodes import (
    ExpectedNodeError,
    NodeDefinition,
    ParameterUpdateMode,
)
from synesthesia_machine.nodes.image import create_image_definitions
from synesthesia_machine.nodes.registry import NodeRegistry

NODE_ID = UUID("00000000-0000-0000-0000-000000005060")
OTHER_CLOCK = UUID("00000000-0000-0000-0000-000000005061")
BATCH5_IDS = (
    "synmachine.image.blend_images",
    "synmachine.image.separate_channels",
    "synmachine.image.combine_channels",
    "synmachine.image.to_luminance",
)
BLEND_MODES = ("NORMAL", "ADD", "MULTIPLY", "SCREEN", "DIFFERENCE", "LIGHTEN", "DARKEN")
ALPHA_POLICIES = ("COMPOSITE", "PRESERVE_A", "PRESERVE_B")


def _definition(type_id: str) -> NodeDefinition:
    return next(item for item in create_image_definitions() if item.type_id == type_id)


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
    *,
    node_id: UUID = NODE_ID,
) -> Mapping[str, RuntimeValue]:
    definition = _definition(type_id)
    context = next(
        value.context for value in inputs.values() if isinstance(value, (ImageFrame, ChannelFrame))
    )
    return definition.runtime_factory(node_id).process(
        inputs,
        _parameters(definition, overrides),
        context,
    )


def _image(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    color_space: ColorSpace,
    reference: ImageFrame,
    *,
    context: FrameContext | None = None,
) -> ImageFrame:
    descriptor = color_space_descriptor(color_space)
    return ImageFrame(
        read_only_float32(data),
        color_space,
        descriptor.channel_names,
        descriptor.alpha_mode,
        reference.context if context is None else context,
        reference.provenance,
    )


def _channel(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    semantic: ChannelSemantic,
    context: FrameContext,
    *,
    nominal_min: float = 0.0,
    nominal_max: float = 1.0,
    cyclic: bool = False,
) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(data),
        semantic,
        nominal_min,
        nominal_max,
        cyclic,
        context,
    )


def _context_with_clock(reference: FrameContext, clock_id: UUID) -> FrameContext:
    return FrameContext(
        clock_id,
        reference.tick_index,
        reference.source_frame_index,
        reference.source_time_s,
        reference.received_monotonic_ns,
        reference.deadline_monotonic_ns,
        reference.is_realtime,
    )


def _blend(
    a: ImageFrame,
    b: ImageFrame,
    overrides: Mapping[str, object] | None = None,
    *,
    mask: ChannelFrame | None = None,
    connected_opacity: float | None = None,
) -> ImageFrame:
    inputs: dict[str, RuntimeValue] = {"a": a, "b": b}
    if mask is not None:
        inputs["mask"] = mask
    if connected_opacity is not None:
        inputs["opacity"] = connected_opacity
    output = _process("synmachine.image.blend_images", inputs, overrides)["image"]
    assert isinstance(output, ImageFrame)
    return output


def _separate(image: ImageFrame) -> Mapping[str, RuntimeValue]:
    return _process("synmachine.image.separate_channels", {"image": image})


def _combine(
    channels: tuple[ChannelFrame, ...],
    target: ColorSpace,
    *,
    extras: Mapping[str, RuntimeValue] | None = None,
) -> RuntimeValue:
    inputs: dict[str, RuntimeValue] = {
        f"channel_{index}": channel for index, channel in enumerate(channels, start=1)
    }
    inputs.update(extras or {})
    return _process(
        "synmachine.image.combine_channels",
        inputs,
        {"target_colour_space": target.value},
    )["image"]


def test_batch5_metadata_has_exact_order_ports_defaults_and_policies() -> None:
    definitions = [item for item in create_image_definitions() if item.type_id in BATCH5_IDS]
    assert tuple(item.type_id for item in definitions) == BATCH5_IDS

    blend, separate, combine, luminance = definitions
    assert tuple(port.id for port in blend.inputs) == ("a", "b", "mask")
    assert tuple(port.required for port in blend.inputs) == (True, True, False)
    assert tuple(parameter.id for parameter in blend.parameters) == (
        "blend_mode",
        "opacity",
        "alpha_policy",
    )
    assert tuple(parameter.default for parameter in blend.parameters) == (
        "NORMAL",
        1.0,
        "COMPOSITE",
    )
    assert blend.parameters[0].choices == BLEND_MODES
    assert blend.parameters[1].connectable
    assert blend.parameters[1].connected_port_type is PortType.FLOAT
    assert blend.parameters[2].choices == ALPHA_POLICIES

    assert tuple(port.id for port in separate.outputs) == (
        "channel_1",
        "channel_2",
        "channel_3",
        "channel_4",
    )
    assert not separate.parameters

    assert tuple(port.id for port in combine.inputs) == (
        "channel_1",
        "channel_2",
        "channel_3",
        "channel_4",
    )
    assert all(not port.required for port in combine.inputs)
    assert combine.handles_no_data
    target = combine.parameters[0]
    assert target.id == "target_colour_space"
    assert target.default == ColorSpace.SRGB.value
    assert target.update_mode is ParameterUpdateMode.RECOMPILE
    assert target.choices == tuple(space.value for space in ColorSpace)

    assert tuple(port.id for port in luminance.inputs) == ("image",)
    assert tuple(port.id for port in luminance.outputs) == ("channel",)
    assert not luminance.parameters


@pytest.mark.parametrize(
    "type_id",
    (
        "synmachine.image.blend_images",
        "synmachine.image.separate_channels",
        "synmachine.image.to_luminance",
    ),
)
def test_scheduler_propagates_no_data_for_standard_batch5_nodes(type_id: str) -> None:
    assert_scheduler_propagates_no_data(_definition(type_id), {})


def test_combine_required_inputs_follow_target_descriptor() -> None:
    definition = _definition("synmachine.image.combine_channels")
    srgb_parameters = _parameters(definition)
    rgba_parameters = _parameters(definition, {"target_colour_space": ColorSpace.RGBA.value})
    assert definition.required_inputs(srgb_parameters) == frozenset(
        {"channel_1", "channel_2", "channel_3"}
    )
    assert definition.required_inputs(rgba_parameters) == frozenset(
        {"channel_1", "channel_2", "channel_3", "channel_4"}
    )


def test_compiler_enforces_descriptor_driven_combine_requirements() -> None:
    registry = NodeRegistry(create_image_definitions())
    compiler = GraphCompiler(registry)
    document = GraphDocument()
    separate_id = document.add_node("synmachine.image.separate_channels")
    combine_id = document.add_node("synmachine.image.combine_channels")
    for index in range(1, 4):
        document.add_connection(
            separate_id,
            f"channel_{index}",
            combine_id,
            f"channel_{index}",
        )
    srgb = compiler.compile(document.snapshot(), demand_roots=(combine_id,))
    assert not any(
        issue.code == "required_input_missing" and issue.node_id == combine_id
        for issue in srgb.report.errors
    )

    document.set_parameter(combine_id, "target_colour_space", ColorSpace.RGBA.value)
    rgba = compiler.compile(document.snapshot(), demand_roots=(combine_id,))
    assert any(
        issue.code == "required_input_missing"
        and issue.node_id == combine_id
        and issue.port_id == "channel_4"
        for issue in rgba.report.errors
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("NORMAL", (0.8, 0.25, 0.4)),
        ("ADD", (1.0, 0.75, 0.5)),
        ("MULTIPLY", (0.16, 0.125, 0.04)),
        ("SCREEN", (0.84, 0.625, 0.46)),
        ("DIFFERENCE", (0.6, 0.25, 0.3)),
        ("LIGHTEN", (0.8, 0.5, 0.4)),
        ("DARKEN", (0.2, 0.25, 0.1)),
    ),
)
def test_blend_modes_apply_without_clipping(
    mode: str,
    expected: tuple[float, float, float],
    rgb_image: ImageFrame,
) -> None:
    a = _image(np.array([[[0.2, 0.5, 0.1]]], dtype=np.float32), ColorSpace.SRGB, rgb_image)
    b = _image(np.array([[[0.8, 0.25, 0.4]]], dtype=np.float32), ColorSpace.SRGB, rgb_image)
    result = _blend(a, b, {"blend_mode": mode})
    assert np.allclose(result.data[0, 0], expected, atol=1e-6)
    assert result.context is a.context and result.provenance is a.provenance
    assert result.data.dtype == np.float32 and result.data.flags.c_contiguous
    assert not result.data.flags.writeable


def test_blend_normal_uses_straight_alpha_source_over(
    rgba_image: ImageFrame,
) -> None:
    a = _image(
        np.array([[[1.0, 0.0, 0.0, 0.5]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    b = _image(
        np.array([[[0.0, 0.0, 1.0, 0.25]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    result = _blend(a, b)
    assert result.alpha_mode is AlphaMode.STRAIGHT
    assert result.data[0, 0, 3] == pytest.approx(0.625)
    assert np.allclose(result.data[0, 0, :3], (0.6, 0.0, 0.4), atol=1e-6)


def test_blend_mask_and_connected_opacity_multiply_without_sanitation(
    rgb_image: ImageFrame,
) -> None:
    a = _image(np.zeros((1, 3, 3), dtype=np.float32), ColorSpace.SRGB, rgb_image)
    b = _image(np.ones((1, 3, 3), dtype=np.float32), ColorSpace.SRGB, rgb_image)
    mask = _channel(
        np.array([[-1.0, 0.5, 2.0]], dtype=np.float32),
        ChannelSemantic.GENERIC,
        a.context,
    )
    result = _blend(a, b, {"opacity": 0.9}, mask=mask, connected_opacity=0.4)
    assert np.allclose(result.data[0, :, 0], (-0.4, 0.2, 0.8), atol=1e-6)


def test_blend_alpha_preservation_policies_are_explicit(
    rgba_image: ImageFrame,
) -> None:
    a = _image(
        np.array([[[0.2, 0.4, 0.6, 0.25]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    b = _image(
        np.array([[[0.8, 0.6, 0.4, 0.75]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    preserve_a = _blend(a, b, {"opacity": 0.5, "alpha_policy": "PRESERVE_A"})
    preserve_b = _blend(a, b, {"opacity": 0.5, "alpha_policy": "PRESERVE_B"})
    assert np.allclose(preserve_a.data[0, 0], (0.5, 0.5, 0.5, 0.25))
    assert np.allclose(preserve_b.data[0, 0], (0.5, 0.5, 0.5, 0.75))


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_blend_rejects_non_finite_masks(
    bad_value: float,
    rgb_image: ImageFrame,
) -> None:
    mask = _channel(
        np.full(rgb_image.data.shape[:2], bad_value, dtype=np.float32),
        ChannelSemantic.GENERIC,
        rgb_image.context,
    )
    with pytest.raises(ExpectedNodeError, match="mask values must be finite") as captured:
        _blend(rgb_image, rgb_image, mask=mask)
    assert captured.value.code == "invalid_blend_images"


def test_blend_rejects_shape_clock_and_descriptor_mismatches(
    rgb_image: ImageFrame,
) -> None:
    different_shape = _image(
        np.zeros((1, 1, 3), dtype=np.float32),
        ColorSpace.SRGB,
        rgb_image,
    )
    with pytest.raises(ExpectedNodeError, match="equal dimensions"):
        _blend(rgb_image, different_shape)

    other_context = _context_with_clock(rgb_image.context, OTHER_CLOCK)
    other_clock = _image(rgb_image.data, ColorSpace.SRGB, rgb_image, context=other_context)
    with pytest.raises(ExpectedNodeError, match="source clock"):
        _blend(rgb_image, other_clock)

    hsv = convert_image(rgb_image, ColorSpace.HSV)
    with pytest.raises(ExpectedNodeError, match="colour descriptors"):
        _blend(rgb_image, hsv)


def test_blend_rejects_mask_shape_and_clock_mismatches(
    rgb_image: ImageFrame,
) -> None:
    wrong_shape = _channel(
        np.ones((1, 1), dtype=np.float32),
        ChannelSemantic.GENERIC,
        rgb_image.context,
    )
    with pytest.raises(ExpectedNodeError, match="mask dimensions"):
        _blend(rgb_image, rgb_image, mask=wrong_shape)

    wrong_clock = _channel(
        np.ones(rgb_image.data.shape[:2], dtype=np.float32),
        ChannelSemantic.GENERIC,
        _context_with_clock(rgb_image.context, OTHER_CLOCK),
    )
    with pytest.raises(ExpectedNodeError, match="mask must share"):
        _blend(rgb_image, rgb_image, mask=wrong_clock)


def test_blend_parameter_validation_rejects_non_finite_opacity() -> None:
    definition = _definition("synmachine.image.blend_images")
    for opacity in (float("nan"), float("inf"), float("-inf")):
        _, errors = definition.parameter_values({"opacity": opacity})
        assert errors
        assert "opacity" in errors[0]


def test_separate_channels_emit_descriptor_views_and_nodata(
    rgba_image: ImageFrame,
) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 0] = np.nan
    data[0, 1, 1] = np.inf
    source = _image(data, ColorSpace.RGBA, rgba_image)
    outputs = _separate(source)
    descriptor = color_space_descriptor(ColorSpace.RGBA)
    for index, expected in enumerate(descriptor.channels, start=1):
        output = outputs[f"channel_{index}"]
        assert isinstance(output, ChannelFrame)
        assert output.semantic is expected.semantic
        assert output.nominal_min == expected.nominal_min
        assert output.nominal_max == expected.nominal_max
        assert output.cyclic is expected.cyclic
        assert output.context is source.context
        assert np.shares_memory(output.data, source.data)
        assert not output.data.flags.writeable
        assert np.array_equal(output.data, source.data[..., index - 1], equal_nan=True)

    rgb_outputs = _separate(convert_image(source, ColorSpace.SRGB))
    assert rgb_outputs["channel_4"] is NoData


def test_separate_and_combine_round_trip_preserves_values_descriptor_and_clock(
    rgba_image: ImageFrame,
) -> None:
    outputs = _separate(rgba_image)
    channels = tuple(
        output
        for index in range(1, 5)
        if isinstance((output := outputs[f"channel_{index}"]), ChannelFrame)
    )
    combined = _combine(channels, ColorSpace.RGBA)
    assert isinstance(combined, ImageFrame)
    assert np.array_equal(combined.data, rgba_image.data)
    assert combined.color_space is ColorSpace.RGBA
    assert combined.channel_names == ("R", "G", "B", "A")
    assert combined.alpha_mode is AlphaMode.STRAIGHT
    assert combined.context is rgba_image.context
    assert combined.provenance == FrameProvenance(NODE_ID, "combine_channels")
    assert combined.data.flags.c_contiguous and not combined.data.flags.writeable
    assert not np.shares_memory(combined.data, rgba_image.data)


def test_combine_preserves_non_finite_channel_values(
    rgb_image: ImageFrame,
) -> None:
    values = (
        np.array([[np.nan, 0.1]], dtype=np.float32),
        np.array([[np.inf, 0.2]], dtype=np.float32),
        np.array([[-np.inf, 0.3]], dtype=np.float32),
    )
    channels = tuple(
        _channel(data, semantic, rgb_image.context)
        for data, semantic in zip(
            values,
            (ChannelSemantic.RED, ChannelSemantic.GREEN, ChannelSemantic.BLUE),
            strict=True,
        )
    )
    combined = _combine(channels, ColorSpace.SRGB)
    assert isinstance(combined, ImageFrame)
    assert np.isnan(combined.data[0, 0, 0])
    assert np.isposinf(combined.data[0, 0, 1])
    assert np.isneginf(combined.data[0, 0, 2])


def test_combine_never_infers_generic_or_misordered_semantics(
    rgb_image: ImageFrame,
) -> None:
    data = np.zeros((2, 2), dtype=np.float32)
    generic = tuple(_channel(data, ChannelSemantic.GENERIC, rgb_image.context) for _ in range(3))
    with pytest.raises(ExpectedNodeError, match="must have RED semantics"):
        _combine(generic, ColorSpace.SRGB)

    misordered = (
        _channel(data, ChannelSemantic.GREEN, rgb_image.context),
        _channel(data, ChannelSemantic.RED, rgb_image.context),
        _channel(data, ChannelSemantic.BLUE, rgb_image.context),
    )
    with pytest.raises(ExpectedNodeError, match="channel_1 must have RED semantics"):
        _combine(misordered, ColorSpace.SRGB)


def test_combine_rejects_dimension_and_clock_mismatches(
    rgb_image: ImageFrame,
) -> None:
    red = _channel(np.zeros((2, 2), dtype=np.float32), ChannelSemantic.RED, rgb_image.context)
    green = _channel(np.zeros((2, 3), dtype=np.float32), ChannelSemantic.GREEN, rgb_image.context)
    blue = _channel(np.zeros((2, 2), dtype=np.float32), ChannelSemantic.BLUE, rgb_image.context)
    with pytest.raises(ExpectedNodeError, match="equal dimensions"):
        _combine((red, green, blue), ColorSpace.SRGB)

    other_context = _context_with_clock(rgb_image.context, OTHER_CLOCK)
    green = _channel(np.zeros((2, 2), dtype=np.float32), ChannelSemantic.GREEN, other_context)
    with pytest.raises(ExpectedNodeError, match="one source clock"):
        _combine((red, green, blue), ColorSpace.SRGB)


def test_combine_ignores_optional_channels_beyond_target_descriptor(
    rgb_image: ImageFrame,
) -> None:
    red = _channel(np.zeros((2, 2), dtype=np.float32), ChannelSemantic.RED, rgb_image.context)
    green = _channel(np.zeros((2, 2), dtype=np.float32), ChannelSemantic.GREEN, rgb_image.context)
    blue = _channel(np.zeros((2, 2), dtype=np.float32), ChannelSemantic.BLUE, rgb_image.context)
    alpha = _channel(np.ones((2, 2), dtype=np.float32), ChannelSemantic.ALPHA, rgb_image.context)
    combined = _combine(
        (red, green, blue),
        ColorSpace.SRGB,
        extras={"channel_4": alpha},
    )
    assert isinstance(combined, ImageFrame)
    assert combined.data.shape == (2, 2, 3)

    combined_without_optional_data = _combine(
        (red, green, blue),
        ColorSpace.SRGB,
        extras={"channel_4": NoData},
    )
    assert isinstance(combined_without_optional_data, ImageFrame)


def test_combine_returns_nodata_if_a_runtime_required_channel_is_nodata(
    rgb_image: ImageFrame,
) -> None:
    red = _channel(
        np.zeros((2, 2), dtype=np.float32),
        ChannelSemantic.RED,
        rgb_image.context,
    )
    result = _process(
        "synmachine.image.combine_channels",
        {"channel_1": red, "channel_2": NoData, "channel_3": NoData},
    )["image"]
    assert result is NoData


def test_luminance_ignores_alpha_and_preserves_clock(
    rgba_image: ImageFrame,
) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[..., 3] = np.nan
    source = _image(data, ColorSpace.RGBA, rgba_image)
    output = _process("synmachine.image.to_luminance", {"image": source})["channel"]
    assert isinstance(output, ChannelFrame)
    expected = image_to_luminance(rgba_image)
    assert np.allclose(output.data, expected.data)
    assert output.semantic is ChannelSemantic.LUMINANCE
    assert output.nominal_min == 0.0 and output.nominal_max == 1.0
    assert not output.cyclic
    assert output.context is source.context
    assert output.data.dtype == np.float32 and output.data.flags.c_contiguous
    assert not output.data.flags.writeable


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_luminance_rejects_non_finite_colour_values(
    bad_value: float,
    rgba_image: ImageFrame,
) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 1] = bad_value
    source = _image(data, ColorSpace.RGBA, rgba_image)
    with pytest.raises(ExpectedNodeError, match="finite colour-channel values") as captured:
        _process("synmachine.image.to_luminance", {"image": source})
    assert captured.value.code == "invalid_image_to_luminance"


def test_batch5_algorithms_do_not_mutate_inputs(
    rgba_image: ImageFrame,
) -> None:
    a_before = rgba_image.data.copy()
    b_data = np.flip(rgba_image.data, axis=1).copy()
    b = _image(b_data, ColorSpace.RGBA, rgba_image)
    b_before = b.data.copy()
    separated = _separate(rgba_image)
    channels = tuple(separated[f"channel_{index}"] for index in range(1, 5))
    assert all(isinstance(channel, ChannelFrame) for channel in channels)
    channel_before = tuple(
        channel.data.copy() for channel in channels if isinstance(channel, ChannelFrame)
    )

    _blend(rgba_image, b)
    _combine(
        tuple(channel for channel in channels if isinstance(channel, ChannelFrame)),
        ColorSpace.RGBA,
    )
    _process("synmachine.image.to_luminance", {"image": rgba_image})

    assert np.array_equal(rgba_image.data, a_before)
    assert np.array_equal(b.data, b_before)
    for channel, before in zip(
        (channel for channel in channels if isinstance(channel, ChannelFrame)),
        channel_before,
        strict=True,
    ):
        assert np.array_equal(channel.data, before)
        assert not channel.data.flags.writeable
