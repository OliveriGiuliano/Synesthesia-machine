"""Compositing and descriptor-backed channel node definitions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

from synesthesia_machine.contracts import (
    ChannelFrame,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    JsonObject,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import (
    AlphaPolicy,
    BlendMode,
    blend_images,
    color_space_descriptor,
    combine_channels,
    image_to_luminance,
    separate_image_channels,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    ParameterUpdateMode,
    StatelessRuntime,
)
from synesthesia_machine.nodes.image.runtime_support import rewrite_colour_space_target


def migrate_separate_channels_v1_to_v2(data: JsonObject) -> JsonObject:
    """Separate Channels v2 dropped the unreachable fourth (alpha) output.

    Sources never carry an alpha channel; the ``channel_4`` socket removal
    and any saved connections to it live in the v3 -> v4 graph migration.
    """

    migrated = deepcopy(data)
    migrated["implementation_version"] = 2
    return migrated


def migrate_combine_channels_v1_to_v2(data: JsonObject) -> JsonObject:
    """Combine Channels v2 dropped the fourth input and the RGBA target."""

    migrated = deepcopy(data)
    rewrite_colour_space_target(migrated)
    migrated["implementation_version"] = 2
    return migrated


class BlendImagesRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        mask_value = inputs.get("mask")
        try:
            result = blend_images(
                cast(ImageFrame, inputs["a"]),
                cast(ImageFrame, inputs["b"]),
                blend_mode=BlendMode(cast(str, parameters["blend_mode"])),
                opacity=cast(float, inputs.get("opacity", parameters["opacity"])),
                alpha_policy=AlphaPolicy(cast(str, parameters["alpha_policy"])),
                mask=None if mask_value is None else cast(ChannelFrame, mask_value),
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_blend_images", str(error)) from error
        return {"image": result}


class SeparateChannelsRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        # Sources never carry an alpha channel, so the node exposes the three
        # descriptor channels; a fourth (alpha) view would be dead clutter.
        channels = separate_image_channels(cast(ImageFrame, inputs["image"]))[:3]
        return {
            f"channel_{index}": NoData if channel is None else channel
            for index, channel in enumerate(channels, start=1)
        }


class CombineChannelsRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        target = ColorSpace(cast(str, parameters["target_colour_space"]))
        required_count = len(color_space_descriptor(target).channels)
        required_values = tuple(
            inputs.get(f"channel_{index}", NoData) for index in range(1, required_count + 1)
        )
        if any(value is NoData for value in required_values):
            return {"image": NoData}
        try:
            channels = tuple(cast(ChannelFrame, value) for value in required_values)
            result = combine_channels(
                channels,
                target,
                FrameProvenance(self.node_id, "combine_channels"),
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_combine_channels", str(error)) from error
        return {"image": result}


class ImageToLuminanceRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        try:
            result = image_to_luminance(cast(ImageFrame, inputs["image"]))
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_image_to_luminance", str(error)) from error
        return {"channel": result}


def _validate_blend_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    opacity = cast(float, parameters["opacity"])
    if not math.isfinite(opacity):
        return ("opacity must be finite",)
    if not 0.0 <= opacity <= 1.0:
        return ("opacity must be between 0 and 1",)
    return ()


def _required_combine_inputs(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    target = ColorSpace(cast(str, parameters["target_colour_space"]))
    return tuple(
        f"channel_{index}" for index in range(1, len(color_space_descriptor(target).channels) + 1)
    )


def create_channel_definitions() -> tuple[NodeDefinition, ...]:
    """Return Batch 5 definitions in their persistent catalogue order."""

    blend_parameters = (
        ParameterSpec(
            "blend_mode",
            "Blend mode",
            PortType.STRING,
            BlendMode.NORMAL.value,
            help_text=(
                "Chooses how the second image is mixed over the first: Normal replaces it by "
                "opacity, Add and Multiply combine the pixel values, Screen lightens, Difference "
                "keeps the gap between them, and Lighten and Darken pick the lighter or darker of "
                "the two."
            ),
            choices=tuple(mode.value for mode in BlendMode),
        ),
        ParameterSpec(
            "opacity",
            "Opacity",
            PortType.FLOAT,
            1.0,
            help_text=(
                "How strongly the second image shows; 0 keeps the first image and 1 replaces it. "
                "A connected mask channel can vary it across the picture."
            ),
            minimum=0.0,
            maximum=1.0,
            connectable=True,
            connected_port_type=PortType.FLOAT,
            editor_hint=ParameterEditorHint.SLIDER,
        ),
        ParameterSpec(
            "alpha_policy",
            "Alpha policy",
            PortType.STRING,
            AlphaPolicy.COMPOSITE.value,
            help_text=(
                "Chooses how transparency is handled: Composite merges the transparency of both "
                "images, Preserve A keeps the first image's transparency, and Preserve B keeps "
                "the second's."
            ),
            choices=tuple(policy.value for policy in AlphaPolicy),
        ),
    )
    return (
        NodeDefinition(
            "synmachine.image.blend_images",
            1,
            "Blend Images",
            "Image / Compositing",
            "Mixes two images together. You choose how much of the top image shows and how the "
            "colours combine.",
            (
                InputPortSpec("a", "A", PortType.IMAGE),
                InputPortSpec("b", "B", PortType.IMAGE),
                InputPortSpec("mask", "Mask", PortType.CHANNEL, required=False),
            ),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            blend_parameters,
            ExecutionKind.STATELESS,
            BlendImagesRuntime,
            aliases=("blend", "composite", "mix images"),
            parameter_validator=_validate_blend_parameters,
        ),
        NodeDefinition(
            "synmachine.image.separate_channels",
            2,
            "Separate Channels",
            "Image / Channel",
            "Splits an image into its individual channels, for example R, G and B, so you can use "
            "them separately.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            tuple(
                OutputPortSpec(f"channel_{index}", f"Channel {index}", PortType.CHANNEL)
                for index in range(1, 4)
            ),
            (),
            ExecutionKind.STATELESS,
            SeparateChannelsRuntime,
            aliases=("split channels", "rgb channels", "hsv channels"),
            migrations={1: migrate_separate_channels_v1_to_v2},
        ),
        NodeDefinition(
            "synmachine.image.combine_channels",
            2,
            "Combine Channels",
            "Image / Channel",
            "Joins separate channels back together into one image in the colour model you choose.",
            tuple(
                InputPortSpec(
                    f"channel_{index}",
                    f"Channel {index}",
                    PortType.CHANNEL,
                    required=False,
                )
                for index in range(1, 4)
            ),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            (
                ParameterSpec(
                    "target_colour_space",
                    "Target colour space",
                    PortType.STRING,
                    ColorSpace.SRGB.value,
                    help_text=(
                        "Colour space that describes how the connected channels are combined into "
                        "an image."
                    ),
                    # RGBA is not offered: the app's sources never carry an
                    # alpha channel, so a fourth input would be dead weight.
                    choices=tuple(
                        space.value for space in ColorSpace if space is not ColorSpace.RGBA
                    ),
                    update_mode=ParameterUpdateMode.RECOMPILE,
                ),
            ),
            ExecutionKind.STATELESS,
            CombineChannelsRuntime,
            handles_no_data=True,
            required_input_resolver=_required_combine_inputs,
            aliases=("merge channels", "assemble image", "channels to image"),
            migrations={1: migrate_combine_channels_v1_to_v2},
        ),
        NodeDefinition(
            "synmachine.image.to_luminance",
            1,
            "Image to Luminance",
            "Image / Channel",
            "Turns a colour image into a single black-and-white brightness channel.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("channel", "Luminance", PortType.CHANNEL),),
            (),
            ExecutionKind.STATELESS,
            ImageToLuminanceRuntime,
            aliases=("grayscale", "greyscale", "luma"),
        ),
    )


__all__ = ["create_channel_definitions"]
