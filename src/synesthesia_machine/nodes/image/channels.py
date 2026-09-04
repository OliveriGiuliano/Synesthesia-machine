"""Compositing and descriptor-backed channel node definitions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from synesthesia_machine.contracts import (
    ColorSpace,
    FrameContext,
    FrameProvenance,
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
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    channel_value,
    image_value,
    number_value,
    text_value,
)


class BlendImagesRuntime(StatelessImageRuntime):
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
                image_value(inputs["a"]),
                image_value(inputs["b"]),
                blend_mode=BlendMode(text_value(parameters["blend_mode"])),
                opacity=number_value(inputs.get("opacity", parameters["opacity"])),
                alpha_policy=AlphaPolicy(text_value(parameters["alpha_policy"])),
                mask=None if mask_value is None else channel_value(mask_value),
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_blend_images", str(error)) from error
        return {"image": result}


class SeparateChannelsRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        # Sources never carry an alpha channel, so the node exposes the three
        # descriptor channels; a fourth (alpha) view would be dead clutter.
        channels = separate_image_channels(image_value(inputs["image"]))[:3]
        return {
            f"channel_{index}": NoData if channel is None else channel
            for index, channel in enumerate(channels, start=1)
        }


class CombineChannelsRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        target = ColorSpace(text_value(parameters["target_colour_space"]))
        required_count = len(color_space_descriptor(target).channels)
        required_values = tuple(
            inputs.get(f"channel_{index}", NoData) for index in range(1, required_count + 1)
        )
        if any(value is NoData for value in required_values):
            return {"image": NoData}
        try:
            channels = tuple(channel_value(value) for value in required_values)
            result = combine_channels(
                channels,
                target,
                FrameProvenance(self.node_id, "combine_channels"),
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_combine_channels", str(error)) from error
        return {"image": result}


class ImageToLuminanceRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        try:
            result = image_to_luminance(image_value(inputs["image"]))
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_image_to_luminance", str(error)) from error
        return {"channel": result}


def _validate_blend_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    opacity = number_value(parameters["opacity"])
    if not math.isfinite(opacity):
        return ("opacity must be finite",)
    if not 0.0 <= opacity <= 1.0:
        return ("opacity must be between 0 and 1",)
    return ()


def _required_combine_inputs(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    target = ColorSpace(text_value(parameters["target_colour_space"]))
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
