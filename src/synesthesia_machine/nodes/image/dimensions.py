"""Phase 5 image-dimension and geometric utility node definitions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from synesthesia_machine.contracts import (
    ColorValue,
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
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
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    boolean_value,
    color_value,
    image_value,
    integer_value,
    number_value,
    text_value,
)


class ResizeRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            result = resize_image(
                image_value(inputs["image"]),
                integer_value(inputs.get("width", parameters["width"])),
                integer_value(inputs.get("height", parameters["height"])),
                preserve_aspect=boolean_value(parameters["preserve_aspect"]),
                fit_mode=FitMode(text_value(parameters["fit_mode"])),
                interpolation=Interpolation(text_value(parameters["interpolation"])),
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_resize", str(error)) from error
        return {"image": result}


class CropRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            result = crop_image(
                image_value(inputs["image"]),
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
            result = flip_image(
                image_value(inputs["image"]), FlipMode(text_value(parameters["mode"]))
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_flip", str(error)) from error
        return {"image": result}


class RotateRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            result = rotate_image(
                image_value(inputs["image"]),
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
        return {"image": result}


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
    image_input = (InputPortSpec("image", "Image", PortType.IMAGE),)
    image_output = (OutputPortSpec("image", "Image", PortType.IMAGE),)
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
                    "pad_colour", "Pad colour", PortType.COLOR, ColorValue(0.0, 0.0, 0.0, 0.0)
                ),
            ),
            ExecutionKind.STATELESS,
            CropRuntime,
            aliases=("trim", "bounds", "roi"),
            parameter_validator=_validate_crop,
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
                ),
            ),
            ExecutionKind.STATELESS,
            RotateRuntime,
            aliases=("turn", "angle", "transform"),
        ),
    )


__all__ = ["create_dimension_definitions"]
