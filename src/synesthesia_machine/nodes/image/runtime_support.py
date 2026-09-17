"""Shared adapter machinery for the image-or-channel node families.

Filter, adjustment, and dimension nodes all present the same "dynamic
image or channel" contract: one ``image`` port that accepts either frame
kind plus the channel-selection and channel-bridge rules. This module
owns that machinery once; the family modules keep only their per-node
algorithms and definitions.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import cast
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media import ChannelSelection
from synesthesia_machine.nodes import (
    ParameterSpec,
    PureFunctionRuntime,
    TypeVariable,
)
from synesthesia_machine.nodes.base import PureFunctionProcessor

IMAGE_OR_CHANNEL = TypeVariable("IMAGE_OR_CHANNEL", frozenset({PortType.IMAGE, PortType.CHANNEL}))

type ImageSource = ImageFrame | ChannelFrame

type FilterProcessor = Callable[
    [ImageFrame, Mapping[str, RuntimeValue], Mapping[str, ParameterValue], FrameContext],
    ImageFrame,
]

type AdjustmentProcessor = Callable[..., ImageSource]


class FilterRuntime(PureFunctionRuntime):
    """Stateless filter node that applies the channel colour-override rule."""

    def __init__(
        self,
        node_id: UUID,
        processor: FilterProcessor,
        error_code: str,
    ) -> None:
        super().__init__(
            node_id,
            processor=_filter_dispatch(processor),
            error_code=error_code,
            exceptions=(TypeError, ValueError),
        )


class AdjustmentRuntime(PureFunctionRuntime):
    """Stateless adjustment node that passes the source frame through unchanged."""

    def __init__(
        self,
        node_id: UUID,
        processor: AdjustmentProcessor,
        error_code: str,
    ) -> None:
        super().__init__(
            node_id,
            processor=_adjustment_dispatch(processor),
            error_code=error_code,
        )


def _filter_dispatch(processor: FilterProcessor) -> PureFunctionProcessor:
    def process(
        node_id: UUID,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        source = image_source(inputs["image"])
        # The COLOUR override is only meaningful for a single-channel source;
        # build the throwaway parameter copy lazily so the common ImageFrame
        # path passes the compiled parameter mapping through untouched.
        if isinstance(source, ChannelFrame) and "channels" in parameters:
            effective_parameters = dict(parameters)
            effective_parameters["channels"] = ChannelSelection.COLOUR.value
            result = processor(
                as_colour_image(source, node_id), inputs, effective_parameters, context
            )
        else:
            result = processor(as_colour_image(source, node_id), inputs, parameters, context)
        return {"image": restore_frame_type(source, result)}

    return process


def _adjustment_dispatch(processor: AdjustmentProcessor) -> PureFunctionProcessor:
    def process(
        node_id: UUID,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del node_id, context
        source = image_source(inputs["image"])
        return {"image": processor(source, inputs, parameters)}

    return process


def image_source(value: object) -> ImageSource:
    """Return the input frame, raising TypeError for values that are neither kind."""

    if isinstance(value, (ImageFrame, ChannelFrame)):
        return value
    raise TypeError(f"Expected image or channel, got {type(value).__name__}")


def as_colour_image(value: ImageSource, node_id: UUID) -> ImageFrame:
    """View a channel source as a replicated RGB image (filter family)."""

    if isinstance(value, ImageFrame):
        return value
    replicated = np.repeat(value.data[..., None], 3, axis=2)
    return ImageFrame(
        read_only_float32(replicated),
        ColorSpace.LINEAR_RGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        value.context,
        FrameProvenance(node_id, "channel_adapter"),
    )


def as_value_image(value: ImageSource, node_id: UUID) -> ImageFrame:
    """View a channel source as a single-channel "value" image (dimension family)."""

    if isinstance(value, ImageFrame):
        return value
    return ImageFrame(
        read_only_float32(value.data[..., None]),
        ColorSpace.LINEAR_RGB,
        ("value",),
        AlphaMode.NONE,
        value.context,
        FrameProvenance(node_id, "channel_adapter"),
    )


def restore_frame_type(source: ImageSource, result: ImageFrame) -> ImageSource:
    """Carry a frame produced on the image side back to the source frame kind."""

    if isinstance(source, ImageFrame):
        return result
    return ChannelFrame(
        read_only_float32(result.data[..., 0]),
        source.semantic,
        source.nominal_min,
        source.nominal_max,
        source.cyclic,
        source.context,
    )


def channel_like(channel: ChannelFrame, data: np.ndarray) -> ChannelFrame:
    """Wrap new pixel data in the source channel's semantic and range metadata."""

    return ChannelFrame(
        read_only_float32(np.asarray(data, dtype=np.float32)),
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )


def dynamic_number(
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    parameter_id: str,
) -> float:
    """Read a dynamic float: the connected input wins, otherwise the literal parameter."""

    return cast(float, inputs.get(parameter_id, parameters[parameter_id]))


def channel_selection_parameter() -> ParameterSpec:
    return ParameterSpec(
        "channels",
        "Channels",
        PortType.STRING,
        ChannelSelection.COLOUR.value,
        help_text=(
            "Chooses which channels receive the change: Colour affects the colour channels, All "
            "channels affects every channel, and Channel 1 to 3 affect only that channel."
        ),
        # Sources never carry a fourth (alpha) channel, so CHANNEL_4 is not
        # offered as a selectable target.
        choices=tuple(
            selection.value
            for selection in ChannelSelection
            if selection is not ChannelSelection.CHANNEL_4
        ),
        applicable_input_types=(PortType.IMAGE,),
    )


def combined_parameter_validator(
    parameter_specs: tuple[ParameterSpec, ...],
    validator: Callable[[Mapping[str, ParameterValue]], Sequence[str]] | None,
) -> Callable[[Mapping[str, ParameterValue]], Sequence[str]]:
    """Combine the float-finiteness rule with an optional family validator."""

    float_parameter_ids = tuple(
        parameter.id for parameter in parameter_specs if parameter.value_type is PortType.FLOAT
    )

    def validate(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
        finite_errors = tuple(
            f"{parameter_id} must be finite"
            for parameter_id in float_parameter_ids
            if not math.isfinite(cast(float, parameters[parameter_id]))
        )
        if finite_errors:
            return finite_errors
        return () if validator is None else validator(parameters)

    return validate


def dynamic_image_channel_resolver(
    port_id: str, is_output: bool, parameters: Mapping[str, ParameterValue]
) -> TypeVariable:
    del port_id, is_output, parameters
    return IMAGE_OR_CHANNEL


__all__ = [
    "IMAGE_OR_CHANNEL",
    "AdjustmentProcessor",
    "AdjustmentRuntime",
    "FilterProcessor",
    "FilterRuntime",
    "ImageSource",
    "as_colour_image",
    "as_value_image",
    "channel_like",
    "channel_selection_parameter",
    "combined_parameter_validator",
    "dynamic_image_channel_resolver",
    "dynamic_number",
    "image_source",
    "restore_frame_type",
]
