"""Behaviour tests for the shared image-or-channel adapter machinery."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media import ChannelSelection
from synesthesia_machine.nodes import (
    ExpectedNodeError,
    ParameterSpec,
)
from synesthesia_machine.nodes.image.runtime_support import (
    IMAGE_OR_CHANNEL,
    AdjustmentRuntime,
    FilterRuntime,
    as_colour_image,
    as_value_image,
    channel_like,
    channel_selection_parameter,
    combined_parameter_validator,
    dynamic_image_channel_resolver,
    dynamic_number,
    image_source,
    restore_frame_type,
)

NODE_ID = UUID("00000000-0000-0000-0000-000000005060")


def _channel(context: FrameContext) -> ChannelFrame:
    data = np.arange(4 * 6, dtype=np.float32).reshape(4, 6) / np.float32(23.0)
    return ChannelFrame(
        read_only_float32(data),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        context,
    )


def _image(context: FrameContext) -> ImageFrame:
    data = np.arange(4 * 6 * 3, dtype=np.float32).reshape(4, 6, 3) / np.float32(71.0)
    return ImageFrame(
        read_only_float32(data),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(NODE_ID, "adapter-test"),
    )


def test_image_source_accepts_both_frame_kinds(image_context: FrameContext) -> None:
    image = _image(image_context)
    channel = _channel(image_context)
    assert image_source(image) is image
    assert image_source(channel) is channel


def test_image_source_rejects_other_values() -> None:
    with pytest.raises(TypeError, match="Expected image or channel, got float"):
        image_source(1.0)


def test_as_colour_image_replicates_channel_into_rgb(image_context: FrameContext) -> None:
    channel = _channel(image_context)
    result = as_colour_image(channel, NODE_ID)
    assert isinstance(result, ImageFrame)
    assert result.data.shape == (*channel.data.shape, 3)
    np.testing.assert_array_equal(result.data[..., 0], channel.data)
    assert result.color_space is ColorSpace.LINEAR_RGB
    assert result.channel_names == ("R", "G", "B")
    assert result.provenance.source_kind == "channel_adapter"


def test_as_colour_image_passes_image_through(image_context: FrameContext) -> None:
    image = _image(image_context)
    assert as_colour_image(image, NODE_ID) is image


def test_as_value_image_keeps_single_value_channel(image_context: FrameContext) -> None:
    channel = _channel(image_context)
    result = as_value_image(channel, NODE_ID)
    assert result.data.shape == (*channel.data.shape, 1)
    np.testing.assert_array_equal(result.data[..., 0], channel.data)
    assert result.channel_names == ("value",)
    assert result.provenance.source_kind == "channel_adapter"


def test_restore_frame_type_keeps_image_and_returns_channel(
    image_context: FrameContext,
) -> None:
    image = _image(image_context)
    assert restore_frame_type(image, image) is image

    channel = _channel(image_context)
    produced = _image(image_context)
    result = restore_frame_type(channel, produced)
    assert isinstance(result, ChannelFrame)
    assert result.semantic is channel.semantic
    assert result.nominal_min == channel.nominal_min
    assert result.nominal_max == channel.nominal_max
    assert result.cyclic is channel.cyclic
    np.testing.assert_array_equal(result.data, produced.data[..., 0])


def test_channel_like_preserves_source_metadata(image_context: FrameContext) -> None:
    channel = _channel(image_context)
    result = channel_like(channel, np.full((4, 6), np.float32(0.5)))
    assert isinstance(result, ChannelFrame)
    assert result.semantic is channel.semantic
    assert result.nominal_min == channel.nominal_min
    assert result.nominal_max == channel.nominal_max
    assert (result.data == np.float32(0.5)).all()


def test_dynamic_number_prefers_connected_input() -> None:
    inputs: Mapping[str, RuntimeValue] = {"offset": 0.25}
    parameters = {"offset": 0.5}
    assert dynamic_number(inputs, parameters, "offset") == 0.25
    assert dynamic_number({}, parameters, "offset") == 0.5


def test_filter_runtime_applies_colour_override_for_channel_sources(
    image_context: FrameContext,
) -> None:
    seen: dict[str, object] = {}

    def processor(
        image: ImageFrame,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, object],
        context: FrameContext,
    ) -> ImageFrame:
        del context
        seen["frame_type"] = type(image).__name__
        seen["channels"] = parameters.get("channels")
        return image

    runtime = FilterRuntime(NODE_ID, processor, "invalid_test_filter")
    channel = _channel(image_context)
    parameters: Mapping[str, object] = {"channels": ChannelSelection.CHANNEL_1.value}
    outputs = runtime.process({"image": channel}, dict(parameters), image_context)
    assert seen["frame_type"] == "ImageFrame"
    assert seen["channels"] == ChannelSelection.COLOUR.value
    assert isinstance(outputs["image"], ChannelFrame)

    image = _image(image_context)
    runtime.process({"image": image}, dict(parameters), image_context)
    assert seen["channels"] == ChannelSelection.CHANNEL_1.value


def test_filter_runtime_reports_unexpected_input_as_expected_error(
    image_context: FrameContext,
) -> None:
    def processor(
        image: ImageFrame,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, object],
        context: FrameContext,
    ) -> ImageFrame:
        del image, inputs, parameters, context
        raise AssertionError("unreachable")

    runtime = FilterRuntime(NODE_ID, processor, "invalid_test_filter")
    with pytest.raises(ExpectedNodeError) as excinfo:
        runtime.process({"image": 1.0}, {}, image_context)
    assert excinfo.value.code == "invalid_test_filter"


def test_adjustment_runtime_passes_channel_source_through(
    image_context: FrameContext,
) -> None:
    seen: dict[str, object] = {}
    channel = _channel(image_context)

    def processor(
        source: ImageFrame | ChannelFrame,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, object],
    ) -> ImageFrame | ChannelFrame:
        del inputs, parameters
        seen["frame_type"] = type(source).__name__
        return source

    runtime = AdjustmentRuntime(NODE_ID, processor, "invalid_test_adjustment")
    outputs = runtime.process({"image": channel}, {}, image_context)
    assert seen["frame_type"] == "ChannelFrame"
    assert outputs["image"] is channel


def test_channel_selection_parameter_excludes_channel_4() -> None:
    parameter = channel_selection_parameter()
    assert parameter.id == "channels"
    assert isinstance(parameter.default, str)
    assert ChannelSelection(parameter.default) is ChannelSelection.COLOUR
    assert "CHANNEL_4" not in tuple(parameter.choices or ())


def test_dynamic_image_channel_resolver_returns_shared_variable() -> None:
    resolver = dynamic_image_channel_resolver
    assert resolver("image", True, {}) is IMAGE_OR_CHANNEL
    assert resolver("image", False, {}) is IMAGE_OR_CHANNEL


def test_combined_parameter_validator_reports_finite_errors() -> None:
    float_spec = ParameterSpec("amount", "Amount", PortType.FLOAT, 1.0)

    def family_validator(parameters: Mapping[str, object]) -> tuple[str, ...]:
        return ("family says no",)

    validate = combined_parameter_validator((float_spec,), family_validator)
    assert validate({"amount": float("inf")}) == ("amount must be finite",)
    assert validate({"amount": 1.0}) == ("family says no",)
    assert combined_parameter_validator((float_spec,), None)({"amount": 1.0}) == ()
