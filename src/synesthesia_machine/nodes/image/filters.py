"""Phase 5 image-filter node definitions and thin runtime adapters."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    NumericMatrix,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media import (
    BorderMode,
    ChannelSelection,
    ConvolutionNormalization,
    MorphKernelShape,
    NoiseType,
    ThresholdMode,
    add_noise_image,
    canny_image,
    convolve_image,
    dilate_image,
    erode_image,
    gaussian_blur_image,
    high_pass_image,
    low_pass_image,
    posterize_image,
    sharpen_image,
    threshold_channel,
    validate_odd_kernel,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    NodeRuntime,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    TypeVariable,
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    boolean_value,
    channel_value,
    image_value,
    integer_value,
    matrix_value,
    number_value,
    text_value,
)

IMAGE_OR_CHANNEL = TypeVariable("IMAGE_OR_CHANNEL", frozenset({PortType.IMAGE, PortType.CHANNEL}))

type FilterProcessor = Callable[
    [
        ImageFrame,
        Mapping[str, RuntimeValue],
        Mapping[str, ParameterValue],
        FrameContext,
    ],
    ImageFrame,
]


class FilterRuntime(StatelessImageRuntime):
    def __init__(self, node_id: UUID, processor: FilterProcessor, error_code: str) -> None:
        super().__init__(node_id)
        self._processor = processor
        self._error_code = error_code

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            source = inputs["image"]
            if not isinstance(source, (ImageFrame, ChannelFrame)):
                raise TypeError(f"Expected image or channel, got {type(source).__name__}")
            effective_parameters = dict(parameters)
            if isinstance(source, ChannelFrame) and "channels" in effective_parameters:
                effective_parameters["channels"] = ChannelSelection.COLOUR.value
            result = self._processor(
                _as_image(source, self.node_id), inputs, effective_parameters, context
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError(self._error_code, str(error)) from error
        return {"image": _restore_type(source, result)}


class ThresholdRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            result = threshold_channel(
                channel_value(inputs["channel"]),
                mode=ThresholdMode(text_value(parameters["mode"])),
                threshold=_number(inputs, parameters, "threshold"),
                maximum=_number(inputs, parameters, "maximum"),
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_threshold", str(error)) from error
        return {"channel": result}


class CannyRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            result = canny_image(
                image_value(inputs["image"]),
                low_threshold=number_value(parameters["low_threshold"]),
                high_threshold=number_value(parameters["high_threshold"]),
                aperture_size=integer_value(parameters["aperture_size"]),
                l2_gradient=boolean_value(parameters["l2_gradient"]),
                pre_blur_sigma=number_value(parameters["pre_blur_sigma"]),
            )
        except (TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_canny", str(error)) from error
        return {"channel": result}


def _factory(processor: FilterProcessor, error_code: str) -> Callable[[UUID], NodeRuntime]:
    def create(node_id: UUID) -> NodeRuntime:
        return FilterRuntime(node_id, processor, error_code)

    return create


def _threshold_factory(node_id: UUID) -> NodeRuntime:
    return ThresholdRuntime(node_id)


def _canny_factory(node_id: UUID) -> NodeRuntime:
    return CannyRuntime(node_id)


def _number(
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    parameter_id: str,
) -> float:
    return number_value(inputs.get(parameter_id, parameters[parameter_id]))


def _gaussian_blur(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return gaussian_blur_image(
        image,
        kernel_width=integer_value(parameters["kernel_width"]),
        kernel_height=integer_value(parameters["kernel_height"]),
        sigma_x=number_value(parameters["sigma_x"]),
        sigma_y=number_value(parameters["sigma_y"]),
        border_mode=BorderMode(text_value(parameters["border_mode"])),
    )


def _sharpen(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return sharpen_image(
        image,
        amount=number_value(parameters["amount"]),
        sigma=number_value(parameters["sigma"]),
        threshold=number_value(parameters["threshold"]),
        border_mode=BorderMode(text_value(parameters["border_mode"])),
    )


def _add_noise(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs
    return add_noise_image(
        image,
        noise_type=NoiseType(text_value(parameters["noise_type"])),
        amount=number_value(parameters["amount"]),
        seed=integer_value(parameters["seed"]),
        monochrome=boolean_value(parameters["monochrome"]),
        animate_seed=boolean_value(parameters["animate_seed"]),
        tick_index=context.tick_index,
        selection=ChannelSelection(text_value(parameters["channels"])),
    )


def _posterize(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return posterize_image(
        image,
        levels=integer_value(parameters["levels"]),
        clamp_input=boolean_value(parameters["clamp_input"]),
        selection=ChannelSelection(text_value(parameters["channels"])),
    )


def _convolve(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return convolve_image(
        image,
        kernel=matrix_value(parameters["kernel"]),
        normalization=ConvolutionNormalization(text_value(parameters["normalization"])),
        scale=number_value(parameters["scale"]),
        delta=number_value(parameters["delta"]),
        border_mode=BorderMode(text_value(parameters["border_mode"])),
    )


def _morph(
    image: ImageFrame,
    parameters: Mapping[str, ParameterValue],
    *,
    dilate: bool,
) -> ImageFrame:
    operation = dilate_image if dilate else erode_image
    return operation(
        image,
        kernel_shape=MorphKernelShape(text_value(parameters["kernel_shape"])),
        kernel_width=integer_value(parameters["kernel_width"]),
        kernel_height=integer_value(parameters["kernel_height"]),
        iterations=integer_value(parameters["iterations"]),
        anchor_x=integer_value(parameters["anchor_x"]),
        anchor_y=integer_value(parameters["anchor_y"]),
        border_mode=BorderMode(text_value(parameters["border_mode"])),
        process_alpha=boolean_value(parameters["process_alpha"]),
    )


def _dilate(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return _morph(image, parameters, dilate=True)


def _erode(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return _morph(image, parameters, dilate=False)


def _high_pass(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return high_pass_image(
        image,
        sigma=number_value(parameters["sigma"]),
        display_offset=number_value(parameters["display_offset"]),
        gain=number_value(parameters["gain"]),
        border_mode=BorderMode(text_value(parameters["border_mode"])),
    )


def _low_pass(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> ImageFrame:
    del inputs, context
    return low_pass_image(
        image,
        sigma=number_value(parameters["sigma"]),
        border_mode=BorderMode(text_value(parameters["border_mode"])),
    )


def _border_parameter() -> ParameterSpec:
    return ParameterSpec(
        "border_mode",
        "Border mode",
        PortType.STRING,
        BorderMode.REFLECT_101.value,
        choices=tuple(mode.value for mode in BorderMode),
    )


def _channel_parameter() -> ParameterSpec:
    return ParameterSpec(
        "channels",
        "Channels",
        PortType.STRING,
        ChannelSelection.COLOUR.value,
        choices=tuple(selection.value for selection in ChannelSelection),
        applicable_input_types=(PortType.IMAGE,),
    )


def _float_parameter(
    parameter_id: str,
    label: str,
    default: float,
    *,
    connectable: bool | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    editor_hint: ParameterEditorHint = ParameterEditorHint.DEFAULT,
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id,
        label,
        PortType.FLOAT,
        default,
        minimum=minimum,
        maximum=maximum,
        connectable=connectable,
        connected_port_type=PortType.FLOAT if connectable else None,
        editor_hint=editor_hint,
    )


def _validate_gaussian(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    width = integer_value(parameters["kernel_width"])
    height = integer_value(parameters["kernel_height"])
    try:
        validate_odd_kernel(width, height)
    except ValueError as error:
        return (str(error),)
    return ()


def _validate_sharpen(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    sigma = number_value(parameters["sigma"])
    threshold = number_value(parameters["threshold"])
    errors: list[str] = []
    if sigma <= 0.0:
        errors.append("sigma must be positive")
    if threshold < 0.0:
        errors.append("threshold must be non-negative")
    return errors


def _validate_noise(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    amount = number_value(parameters["amount"])
    if amount < 0.0:
        return ("amount must be non-negative",)
    if parameters["noise_type"] == NoiseType.SALT_AND_PEPPER.value and amount > 1.0:
        return ("Salt and Pepper amount must not exceed 1",)
    return ()


def _validate_canny(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    low = number_value(parameters["low_threshold"])
    high = number_value(parameters["high_threshold"])
    sigma = number_value(parameters["pre_blur_sigma"])
    errors: list[str] = []
    if not 0.0 <= low <= 1.0 or not 0.0 <= high <= 1.0:
        errors.append("Canny thresholds must be between 0 and 1")
    if high < low:
        errors.append("high threshold must be greater than or equal to low threshold")
    if sigma < 0.0:
        errors.append("pre-blur sigma must be non-negative")
    return errors


def _validate_convolve(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    kernel = matrix_value(parameters["kernel"])
    try:
        validate_odd_kernel(kernel.width, kernel.height, maximum=15)
    except ValueError as error:
        return (str(error),)
    normalization = ConvolutionNormalization(text_value(parameters["normalization"]))
    if normalization is ConvolutionNormalization.SUM_TO_ONE:
        denominator = sum(sum(row) for row in kernel.rows)
    elif normalization is ConvolutionNormalization.ABSOLUTE_SUM_TO_ONE:
        denominator = sum(sum(abs(value) for value in row) for row in kernel.rows)
    else:
        return ()
    return ("kernel normalization denominator must be non-zero",) if denominator == 0.0 else ()


def _validate_morph(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    width = integer_value(parameters["kernel_width"])
    height = integer_value(parameters["kernel_height"])
    try:
        validate_odd_kernel(width, height)
    except ValueError as error:
        return (str(error),)
    anchor_x = integer_value(parameters["anchor_x"])
    anchor_y = integer_value(parameters["anchor_y"])
    if (anchor_x, anchor_y) == (-1, -1):
        return ()
    if anchor_x < 0 or anchor_y < 0 or anchor_x >= width or anchor_y >= height:
        return ("anchor must be (-1, -1) or lie within the kernel",)
    return ()


def _validate_positive(
    parameter_id: str,
) -> Callable[[Mapping[str, ParameterValue]], Sequence[str]]:
    def validate(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
        return (
            ()
            if number_value(parameters[parameter_id]) > 0.0
            else (f"{parameter_id} must be positive",)
        )

    return validate


def _combined_validator(
    parameter_specs: tuple[ParameterSpec, ...],
    validator: Callable[[Mapping[str, ParameterValue]], Sequence[str]] | None,
) -> Callable[[Mapping[str, ParameterValue]], Sequence[str]]:
    float_parameter_ids = tuple(
        parameter.id for parameter in parameter_specs if parameter.value_type is PortType.FLOAT
    )

    def validate(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
        finite_errors = tuple(
            f"{parameter_id} must be finite"
            for parameter_id in float_parameter_ids
            if not math.isfinite(number_value(parameters[parameter_id]))
        )
        if finite_errors:
            return finite_errors
        return () if validator is None else validator(parameters)

    return validate


def _definition(
    type_id: str,
    display_name: str,
    description: str,
    parameters: tuple[ParameterSpec, ...],
    processor: FilterProcessor,
    *,
    aliases: tuple[str, ...] = (),
    validator: Callable[[Mapping[str, ParameterValue]], Sequence[str]] | None = None,
) -> NodeDefinition:
    return NodeDefinition(
        type_id,
        1,
        display_name,
        "Image / Filter",
        description,
        (InputPortSpec("image", "Image / Channel", PortType.IMAGE),),
        (OutputPortSpec("image", "Image / Channel", PortType.IMAGE),),
        parameters,
        ExecutionKind.STATELESS,
        _factory(processor, f"invalid_{type_id.rsplit('.', 1)[1]}"),
        aliases=aliases,
        parameter_validator=_combined_validator(parameters, validator),
        port_type_resolver=_dynamic_image_channel_type,
    )


def _dynamic_image_channel_type(
    port_id: str, is_output: bool, parameters: Mapping[str, ParameterValue]
) -> TypeVariable:
    del port_id, is_output, parameters
    return IMAGE_OR_CHANNEL


def create_filter_definitions() -> tuple[NodeDefinition, ...]:
    gaussian_parameters = (
        ParameterSpec("kernel_width", "Kernel width", PortType.INT, 3, minimum=1, step=2),
        ParameterSpec("kernel_height", "Kernel height", PortType.INT, 3, minimum=1, step=2),
        ParameterSpec("sigma_x", "Sigma X", PortType.FLOAT, 0.0, minimum=0.0),
        ParameterSpec("sigma_y", "Sigma Y", PortType.FLOAT, 0.0, minimum=0.0),
        _border_parameter(),
    )
    sharpen_parameters = (
        ParameterSpec("amount", "Amount", PortType.FLOAT, 1.0),
        ParameterSpec("sigma", "Sigma", PortType.FLOAT, 1.0, minimum=1e-6),
        ParameterSpec("threshold", "Threshold", PortType.FLOAT, 0.0, minimum=0.0),
        _border_parameter(),
    )
    noise_parameters = (
        ParameterSpec(
            "noise_type",
            "Noise type",
            PortType.STRING,
            NoiseType.GAUSSIAN.value,
            choices=tuple(noise_type.value for noise_type in NoiseType),
        ),
        ParameterSpec("amount", "Amount", PortType.FLOAT, 0.05, minimum=0.0),
        ParameterSpec("seed", "Seed", PortType.INT, 0),
        ParameterSpec("monochrome", "Monochrome", PortType.BOOL, False),
        ParameterSpec("animate_seed", "Animate seed", PortType.BOOL, False),
        _channel_parameter(),
    )
    posterize_parameters = (
        ParameterSpec("levels", "Levels", PortType.INT, 4, minimum=2),
        ParameterSpec("clamp_input", "Clamp input", PortType.BOOL, True),
        _channel_parameter(),
    )
    threshold_parameters = (
        ParameterSpec(
            "mode",
            "Mode",
            PortType.STRING,
            ThresholdMode.BINARY.value,
            choices=tuple(mode.value for mode in ThresholdMode),
        ),
        _float_parameter("threshold", "Threshold", 0.5, connectable=True),
        _float_parameter("maximum", "Maximum", 1.0, connectable=True),
    )
    canny_parameters = (
        _float_parameter(
            "low_threshold",
            "Low threshold",
            0.1,
            minimum=0.0,
            maximum=1.0,
            editor_hint=ParameterEditorHint.SLIDER,
        ),
        _float_parameter(
            "high_threshold",
            "High threshold",
            0.3,
            minimum=0.0,
            maximum=1.0,
            editor_hint=ParameterEditorHint.SLIDER,
        ),
        ParameterSpec("aperture_size", "Aperture size", PortType.INT, 3, choices=(3, 5, 7)),
        ParameterSpec("l2_gradient", "L2 gradient", PortType.BOOL, False),
        _float_parameter("pre_blur_sigma", "Pre-blur sigma", 0.0, minimum=0.0),
    )
    convolve_parameters = (
        ParameterSpec("kernel", "Kernel", PortType.MATRIX, NumericMatrix(((1.0,),))),
        ParameterSpec(
            "normalization",
            "Normalization",
            PortType.STRING,
            ConvolutionNormalization.NONE.value,
            choices=tuple(mode.value for mode in ConvolutionNormalization),
        ),
        _float_parameter("scale", "Scale", 1.0),
        _float_parameter("delta", "Delta", 0.0),
        _border_parameter(),
    )
    morph_parameters = (
        ParameterSpec(
            "kernel_shape",
            "Kernel shape",
            PortType.STRING,
            MorphKernelShape.RECTANGLE.value,
            choices=tuple(shape.value for shape in MorphKernelShape),
        ),
        ParameterSpec("kernel_width", "Kernel width", PortType.INT, 3, minimum=1, step=2),
        ParameterSpec("kernel_height", "Kernel height", PortType.INT, 3, minimum=1, step=2),
        ParameterSpec("iterations", "Iterations", PortType.INT, 1, minimum=1),
        ParameterSpec("anchor_x", "Anchor X", PortType.INT, -1, minimum=-1),
        ParameterSpec("anchor_y", "Anchor Y", PortType.INT, -1, minimum=-1),
        _border_parameter(),
        ParameterSpec(
            "process_alpha",
            "Process alpha",
            PortType.BOOL,
            False,
            applicable_input_types=(PortType.IMAGE,),
        ),
    )
    high_pass_parameters = (
        _float_parameter("sigma", "Sigma", 1.0, minimum=1e-6),
        _float_parameter("display_offset", "Display offset", 0.0),
        _float_parameter("gain", "Gain", 1.0),
        _border_parameter(),
    )
    low_pass_parameters = (
        _float_parameter("sigma", "Sigma", 1.0, minimum=1e-6),
        _border_parameter(),
    )
    return (
        _definition(
            "synmachine.image.gaussian_blur",
            "Gaussian Blur",
            "Blur non-alpha channels with explicit odd kernels, sigma, and border behavior.",
            gaussian_parameters,
            _gaussian_blur,
            aliases=("blur", "soften", "gaussian"),
            validator=_validate_gaussian,
        ),
        _definition(
            "synmachine.image.sharpen",
            "Sharpen",
            "Apply unclipped thresholded unsharp masking while preserving alpha.",
            sharpen_parameters,
            _sharpen,
            aliases=("unsharp mask", "detail"),
            validator=_validate_sharpen,
        ),
        _definition(
            "synmachine.image.add_noise",
            "Add Noise",
            "Add deterministic Gaussian, Uniform, or Salt and Pepper noise.",
            noise_parameters,
            _add_noise,
            aliases=("grain", "random", "salt pepper"),
            validator=_validate_noise,
        ),
        _definition(
            "synmachine.image.posterize",
            "Posterize",
            "Quantize selected normalized channels with optional input clamping.",
            posterize_parameters,
            _posterize,
            aliases=("quantize", "colour levels", "color levels"),
        ),
        NodeDefinition(
            "synmachine.image.threshold",
            1,
            "Threshold",
            "Image / Analysis",
            "Apply OpenCV-compatible threshold modes to one channel.",
            (InputPortSpec("channel", "Channel", PortType.CHANNEL),),
            (OutputPortSpec("channel", "Channel", PortType.CHANNEL),),
            threshold_parameters,
            ExecutionKind.STATELESS,
            _threshold_factory,
            aliases=("binary", "cutoff"),
            parameter_validator=_combined_validator(threshold_parameters, None),
        ),
        NodeDefinition(
            "synmachine.image.canny",
            1,
            "Canny",
            "Image / Analysis",
            "Detect luminance edges and emit a normalized channel mask.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("channel", "Channel", PortType.CHANNEL),),
            canny_parameters,
            ExecutionKind.STATELESS,
            _canny_factory,
            aliases=("edges", "edge detection"),
            parameter_validator=_combined_validator(canny_parameters, _validate_canny),
        ),
        _definition(
            "synmachine.image.convolve",
            "Convolve",
            "Filter non-alpha channels with a bounded user-editable matrix.",
            convolve_parameters,
            _convolve,
            aliases=("kernel", "filter 2d"),
            validator=_validate_convolve,
        ),
        _definition(
            "synmachine.image.dilate",
            "Dilate",
            "Apply configurable maximum morphology to image channels.",
            morph_parameters,
            _dilate,
            aliases=("expand", "maximum filter"),
            validator=_validate_morph,
        ),
        _definition(
            "synmachine.image.erode",
            "Erode",
            "Apply configurable minimum morphology to image channels.",
            morph_parameters,
            _erode,
            aliases=("shrink", "minimum filter"),
            validator=_validate_morph,
        ),
        _definition(
            "synmachine.image.high_pass",
            "High Pass",
            "Extract unclipped Gaussian detail with configurable gain and offset.",
            high_pass_parameters,
            _high_pass,
            aliases=("detail", "edges"),
            validator=_validate_positive("sigma"),
        ),
        _definition(
            "synmachine.image.low_pass",
            "Low Pass",
            "Apply a simplified automatic-kernel Gaussian low-pass filter.",
            low_pass_parameters,
            _low_pass,
            aliases=("soften", "blur"),
            validator=_validate_positive("sigma"),
        ),
    )


def _as_image(value: ImageFrame | ChannelFrame, node_id: UUID) -> ImageFrame:
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


def _restore_type(
    source: ImageFrame | ChannelFrame, result: ImageFrame
) -> ImageFrame | ChannelFrame:
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


__all__ = ["create_filter_definitions"]
