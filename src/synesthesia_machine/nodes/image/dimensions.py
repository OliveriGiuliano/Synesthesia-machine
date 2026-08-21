"""Image-dimension and geometric utility node definitions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ColorSpace,
    ColorValue,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media import (
    BorderMode,
    CoordinateMode,
    CropOutOfBounds,
    FitMode,
    FlipMode,
    Interpolation,
    crop_image,
    flip_image,
    resize_image,
    rotate_image,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    TypeVariable,
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    boolean_value,
    color_value,
    integer_value,
    number_value,
    text_value,
)

IMAGE_OR_CHANNEL = TypeVariable("IMAGE_OR_CHANNEL", frozenset({PortType.IMAGE, PortType.CHANNEL}))


class ResizeRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            source = _image_or_channel(inputs["image"])
            result = resize_image(
                _as_image(source, self.node_id),
                integer_value(inputs.get("width", parameters["width"])),
                integer_value(inputs.get("height", parameters["height"])),
                preserve_aspect=boolean_value(parameters["preserve_aspect"]),
                fit_mode=FitMode(text_value(parameters["fit_mode"])),
                interpolation=Interpolation(text_value(parameters["interpolation"])),
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_resize", str(error)) from error
        return {"image": _restore_type(source, result)}


class CropRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            source = _image_or_channel(inputs["image"])
            if isinstance(source, ChannelFrame):
                result: ImageFrame | ChannelFrame = _crop_channel(source, parameters)
            else:
                result = crop_image(
                    source,
                    coordinate_mode=CoordinateMode(text_value(parameters["coordinate_mode"])),
                    left=number_value(parameters["left"]),
                    top=number_value(parameters["top"]),
                    right=number_value(parameters["right"]),
                    bottom=number_value(parameters["bottom"]),
                    out_of_bounds=CropOutOfBounds(text_value(parameters["out_of_bounds"])),
                    pad_colour=color_value(parameters["pad_colour"]),
                )
        except ValueError as error:
            raise ExpectedNodeError("invalid_crop", str(error)) from error
        return {"image": result}


class FlipRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            source = _image_or_channel(inputs["image"])
            result = flip_image(
                _as_image(source, self.node_id), FlipMode(text_value(parameters["mode"]))
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_flip", str(error)) from error
        return {"image": _restore_type(source, result)}


class RotateRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            source = _image_or_channel(inputs["image"])
            result = rotate_image(
                _as_image(source, self.node_id),
                angle_degrees=number_value(
                    inputs.get("angle_degrees", parameters["angle_degrees"])
                ),
                centre_x=number_value(inputs.get("centre_x", parameters["centre_x"])),
                centre_y=number_value(inputs.get("centre_y", parameters["centre_y"])),
                expand_canvas=boolean_value(parameters["expand_canvas"]),
                interpolation=Interpolation(text_value(parameters["interpolation"])),
                border_mode=BorderMode(text_value(parameters["border_mode"])),
                border_colour=color_value(parameters["border_colour"]),
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_rotation", str(error)) from error
        return {"image": _restore_type(source, result)}


def _validate_crop(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    bounds = tuple(number_value(parameters[key]) for key in ("left", "top", "right", "bottom"))
    if not all(math.isfinite(value) for value in bounds):
        return ("crop bounds must be finite",)
    left, top, right, bottom = bounds
    errors: list[str] = []
    if parameters["coordinate_mode"] == CoordinateMode.NORMALIZED.value and any(
        value < 0.0 or value > 1.0 for value in bounds
    ):
        errors.append("normalized crop bounds must be in the range 0..1")
    if right <= left:
        errors.append("right must be greater than left")
    if bottom <= top:
        errors.append("bottom must be greater than top")
    return errors


def create_dimension_definitions() -> tuple[NodeDefinition, ...]:
    image_input = (InputPortSpec("image", "Image / Channel", PortType.IMAGE),)
    image_output = (OutputPortSpec("image", "Image / Channel", PortType.IMAGE),)
    return (
        NodeDefinition(
            "synmachine.image.resize",
            1,
            "Resize",
            "Image / Dimension",
            "Resize with explicit fit, aspect, and interpolation policies; alpha is processed.",
            image_input,
            image_output,
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
            port_type_resolver=_dynamic_image_channel_type,
            aliases=("scale", "image size", "resample"),
        ),
        NodeDefinition(
            "synmachine.image.crop",
            1,
            "Crop",
            "Image / Dimension",
            "Crop normalized or exclusive pixel bounds with explicit out-of-bounds behavior.",
            image_input,
            image_output,
            (
                ParameterSpec(
                    "coordinate_mode",
                    "Coordinates",
                    PortType.STRING,
                    CoordinateMode.NORMALIZED.value,
                    choices=tuple(mode.value for mode in CoordinateMode),
                ),
                ParameterSpec("left", "Left", PortType.FLOAT, 0.0),
                ParameterSpec("top", "Top", PortType.FLOAT, 0.0),
                ParameterSpec("right", "Right", PortType.FLOAT, 1.0),
                ParameterSpec("bottom", "Bottom", PortType.FLOAT, 1.0),
                ParameterSpec(
                    "out_of_bounds",
                    "Out of bounds",
                    PortType.STRING,
                    CropOutOfBounds.CLAMP.value,
                    choices=tuple(policy.value for policy in CropOutOfBounds),
                ),
                ParameterSpec(
                    "pad_colour",
                    "Pad colour",
                    PortType.COLOR,
                    ColorValue(0.0, 0.0, 0.0, 0.0),
                    applicable_input_types=(PortType.IMAGE,),
                ),
            ),
            ExecutionKind.STATELESS,
            CropRuntime,
            aliases=("trim", "bounds", "roi"),
            parameter_validator=_validate_crop,
            port_type_resolver=_dynamic_image_channel_type,
        ),
        NodeDefinition(
            "synmachine.image.flip",
            1,
            "Flip",
            "Image / Utility",
            "Flip every image channel horizontally, vertically, or on both axes.",
            image_input,
            image_output,
            (
                ParameterSpec(
                    "mode",
                    "Mode",
                    PortType.STRING,
                    FlipMode.HORIZONTAL.value,
                    choices=tuple(mode.value for mode in FlipMode),
                ),
            ),
            ExecutionKind.STATELESS,
            FlipRuntime,
            aliases=("mirror", "reverse"),
            port_type_resolver=_dynamic_image_channel_type,
        ),
        NodeDefinition(
            "synmachine.image.rotate",
            1,
            "Rotate",
            "Image / Utility",
            "Rotate counter-clockwise around a normalized centre with explicit borders.",
            image_input,
            image_output,
            (
                ParameterSpec(
                    "angle_degrees",
                    "Angle (degrees)",
                    PortType.FLOAT,
                    0.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                ),
                ParameterSpec(
                    "centre_x",
                    "Centre X",
                    PortType.FLOAT,
                    0.5,
                    minimum=0.0,
                    maximum=1.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "centre_y",
                    "Centre Y",
                    PortType.FLOAT,
                    0.5,
                    minimum=0.0,
                    maximum=1.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec("expand_canvas", "Expand canvas", PortType.BOOL, False),
                ParameterSpec(
                    "interpolation",
                    "Interpolation",
                    PortType.STRING,
                    Interpolation.AUTO.value,
                    choices=tuple(mode.value for mode in Interpolation),
                ),
                ParameterSpec(
                    "border_mode",
                    "Border mode",
                    PortType.STRING,
                    BorderMode.REFLECT_101.value,
                    choices=tuple(mode.value for mode in BorderMode),
                ),
                ParameterSpec(
                    "border_colour",
                    "Border colour",
                    PortType.COLOR,
                    ColorValue(0.0, 0.0, 0.0, 0.0),
                    applicable_input_types=(PortType.IMAGE,),
                ),
            ),
            ExecutionKind.STATELESS,
            RotateRuntime,
            aliases=("turn", "angle", "transform"),
            port_type_resolver=_dynamic_image_channel_type,
        ),
    )


def _dynamic_image_channel_type(
    port_id: str, is_output: bool, parameters: Mapping[str, ParameterValue]
) -> TypeVariable:
    del port_id, is_output, parameters
    return IMAGE_OR_CHANNEL


def _image_or_channel(value: object) -> ImageFrame | ChannelFrame:
    if isinstance(value, (ImageFrame, ChannelFrame)):
        return value
    raise TypeError(f"Expected image or channel, got {type(value).__name__}")


def _as_image(value: ImageFrame | ChannelFrame, node_id: UUID) -> ImageFrame:
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


def _restore_type(
    source: ImageFrame | ChannelFrame, result: ImageFrame
) -> ImageFrame | ChannelFrame:
    if isinstance(source, ImageFrame):
        return result
    data = result.data[..., 0]
    return ChannelFrame(
        read_only_float32(data),
        source.semantic,
        source.nominal_min,
        source.nominal_max,
        source.cyclic,
        source.context,
    )


def _crop_channel(channel: ChannelFrame, parameters: Mapping[str, ParameterValue]) -> ChannelFrame:
    height, width = channel.data.shape
    bounds = [number_value(parameters[key]) for key in ("left", "top", "right", "bottom")]
    if CoordinateMode(text_value(parameters["coordinate_mode"])) is CoordinateMode.NORMALIZED:
        left, top, right, bottom = (
            round(bounds[0] * width),
            round(bounds[1] * height),
            round(bounds[2] * width),
            round(bounds[3] * height),
        )
    else:
        left, top, right, bottom = (round(value) for value in bounds)
    target_width, target_height = right - left, bottom - top
    if target_width <= 0 or target_height <= 0:
        raise ValueError("crop bounds must produce a non-empty channel")
    outside = left < 0 or top < 0 or right > width or bottom > height
    policy = CropOutOfBounds(text_value(parameters["out_of_bounds"]))
    if policy is CropOutOfBounds.ERROR and outside:
        raise ValueError("crop bounds extend outside the source channel")
    source_left, source_top = max(0, left), max(0, top)
    source_right, source_bottom = min(width, right), min(height, bottom)
    if policy is CropOutOfBounds.CLAMP:
        if source_right <= source_left or source_bottom <= source_top:
            raise ValueError("clamped crop is empty")
        data = channel.data[source_top:source_bottom, source_left:source_right]
    else:
        data = np.zeros((target_height, target_width), dtype=np.float32)
        if source_right > source_left and source_bottom > source_top:
            destination_left, destination_top = source_left - left, source_top - top
            data[
                destination_top : destination_top + source_bottom - source_top,
                destination_left : destination_left + source_right - source_left,
            ] = channel.data[source_top:source_bottom, source_left:source_right]
    return ChannelFrame(
        read_only_float32(np.asarray(data, dtype=np.float32)),
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )


__all__ = ["create_dimension_definitions"]
