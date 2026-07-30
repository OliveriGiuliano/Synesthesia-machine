"""Resize, explicit colour conversion, channel separation, and luminance nodes."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts.runtime_values import (
    ChannelFrame,
    ColorSpace,
    FrameContext,
    ImageFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import (
    FitMode,
    Interpolation,
    color_space_descriptor,
    convert_image,
    image_to_luminance,
    resize_image,
)
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ResetReason,
)


class _RuntimeBase:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class ResizeRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        image = _image(inputs["image"])
        width = _integer(inputs.get("width", parameters["width"]))
        height = _integer(inputs.get("height", parameters["height"]))
        try:
            result = resize_image(
                image,
                width,
                height,
                preserve_aspect=_boolean(parameters["preserve_aspect"]),
                fit_mode=FitMode(_text(parameters["fit_mode"])),
                interpolation=Interpolation(_text(parameters["interpolation"])),
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_resize", str(error)) from error
        return {"image": result}


class ChangeColourSpaceRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        return {
            "image": convert_image(
                _image(inputs["image"]), ColorSpace(_text(parameters["target_colour_space"]))
            )
        }


class SeparateChannelsRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        image = _image(inputs["image"])
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


class ImageToLuminanceRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        return {"channel": image_to_luminance(_image(inputs["image"]))}


def create_image_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            "synmachine.image.resize",
            1,
            "Resize",
            "Image / Dimension",
            "Resize an image with explicit aspect, fit, and interpolation policies.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            (
                ParameterSpec(
                    "width",
                    "Width",
                    PortType.INT,
                    500,
                    minimum=1,
                    maximum=8192,
                    connectable=True,
                    connected_port_type=PortType.INT,
                ),
                ParameterSpec(
                    "height",
                    "Height",
                    PortType.INT,
                    500,
                    minimum=1,
                    maximum=8192,
                    connectable=True,
                    connected_port_type=PortType.INT,
                ),
                ParameterSpec("preserve_aspect", "Preserve aspect", PortType.BOOL, True),
                ParameterSpec(
                    "fit_mode",
                    "Fit mode",
                    PortType.STRING,
                    FitMode.CONTAIN.value,
                    choices=tuple(mode.value for mode in FitMode),
                ),
                ParameterSpec(
                    "interpolation",
                    "Interpolation",
                    PortType.STRING,
                    Interpolation.AUTO.value,
                    choices=tuple(mode.value for mode in Interpolation),
                ),
            ),
            ExecutionKind.STATELESS,
            ResizeRuntime,
            aliases=("scale", "image size", "resample"),
        ),
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


def _image(value: object) -> ImageFrame:
    if isinstance(value, ImageFrame):
        return value
    raise TypeError(f"Expected ImageFrame, got {type(value).__name__}")


def _integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected integer, got {type(value).__name__}")


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string, got {type(value).__name__}")
