"""Phase 3 image-node facade retained while Phase 5 splits cohesive node families."""

from __future__ import annotations

from collections.abc import Mapping

from synesthesia_machine.contracts.runtime_values import (
    ChannelFrame,
    ColorSpace,
    FrameContext,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import (
    color_space_descriptor,
    convert_image,
    image_to_luminance,
)
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
)
from synesthesia_machine.nodes.image.adjustments import create_adjustment_definitions
from synesthesia_machine.nodes.image.dimensions import create_dimension_definitions
from synesthesia_machine.nodes.image.filters import create_filter_definitions
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    image_value,
    text_value,
)


class ChangeColourSpaceRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        return {
            "image": convert_image(
                image_value(inputs["image"]),
                ColorSpace(text_value(parameters["target_colour_space"])),
            )
        }


class SeparateChannelsRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        image = image_value(inputs["image"])
        descriptor = color_space_descriptor(image.color_space)
        outputs: dict[str, RuntimeValue] = {}
        for index in range(4):
            port_id = f"channel_{index + 1}"
            if index >= len(descriptor.channels):
                outputs[port_id] = NoData
                continue
            channel = descriptor.channels[index]
            view = image.data[..., index]
            view.flags.writeable = False
            outputs[port_id] = ChannelFrame(
                view,
                channel.semantic,
                channel.nominal_min,
                channel.nominal_max,
                channel.cyclic,
                image.context,
            )
        return outputs


class ImageToLuminanceRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        return {"channel": image_to_luminance(image_value(inputs["image"]))}


def create_image_definitions() -> tuple[NodeDefinition, ...]:
    return (
        *create_dimension_definitions(),
        *create_adjustment_definitions(),
        *create_filter_definitions(),
        NodeDefinition(
            "synmachine.image.change_colour_space",
            1,
            "Change Colour Space",
            "Image / Utility",
            "Explicitly convert image colour values and descriptor metadata.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            (
                ParameterSpec(
                    "target_colour_space",
                    "Target colour space",
                    PortType.STRING,
                    ColorSpace.HSV.value,
                    choices=tuple(space.value for space in ColorSpace),
                ),
            ),
            ExecutionKind.STATELESS,
            ChangeColourSpaceRuntime,
            aliases=("convert colour", "convert color", "hsv", "lab", "ycrcb"),
        ),
        NodeDefinition(
            "synmachine.image.separate_channels",
            1,
            "Separate Channels",
            "Image / Channel",
            "Publish up to four descriptor-backed read-only channel views.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            tuple(
                OutputPortSpec(f"channel_{index}", f"Channel {index}", PortType.CHANNEL)
                for index in range(1, 5)
            ),
            (),
            ExecutionKind.STATELESS,
            SeparateChannelsRuntime,
            aliases=("split channels", "rgb channels", "hsv channels"),
        ),
        NodeDefinition(
            "synmachine.image.to_luminance",
            1,
            "Image to Luminance",
            "Image / Channel",
            "Convert an image to one linear-light luminance channel.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("channel", "Luminance", PortType.CHANNEL),),
            (),
            ExecutionKind.STATELESS,
            ImageToLuminanceRuntime,
            aliases=("grayscale", "greyscale", "luma"),
        ),
    )
