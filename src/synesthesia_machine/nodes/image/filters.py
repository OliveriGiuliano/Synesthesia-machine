"""Phase 5 image-filter node definitions and thin runtime adapters."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    ImageFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import (
    BorderMode,
    ChannelSelection,
    NoiseType,
    add_noise_image,
    gaussian_blur_image,
    posterize_image,
    sharpen_image,
    validate_odd_kernel,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    NodeRuntime,
    OutputPortSpec,
    ParameterSpec,
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    boolean_value,
    image_value,
    integer_value,
    number_value,
    text_value,
)

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
            result = self._processor(image_value(inputs["image"]), inputs, parameters, context)
        except ValueError as error:
            raise ExpectedNodeError(self._error_code, str(error)) from error
        return {"image": result}


def _factory(processor: FilterProcessor, error_code: str) -> Callable[[UUID], NodeRuntime]:
    def create(node_id: UUID) -> NodeRuntime:
        return FilterRuntime(node_id, processor, error_code)

    return create


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
        (InputPortSpec("image", "Image", PortType.IMAGE),),
        (OutputPortSpec("image", "Image", PortType.IMAGE),),
        parameters,
        ExecutionKind.STATELESS,
        _factory(processor, f"invalid_{type_id.rsplit('.', 1)[1]}"),
        aliases=aliases,
        parameter_validator=_combined_validator(parameters, validator),
    )


def create_filter_definitions() -> tuple[NodeDefinition, ...]:
    gaussian_parameters = (
        ParameterSpec("kernel_width", "Kernel width", PortType.INT, 3, minimum=1),
        ParameterSpec("kernel_height", "Kernel height", PortType.INT, 3, minimum=1),
        ParameterSpec("sigma_x", "Sigma X", PortType.FLOAT, 0.0, minimum=0.0),
        ParameterSpec("sigma_y", "Sigma Y", PortType.FLOAT, 0.0, minimum=0.0),
        _border_parameter(),
    )
    sharpen_parameters = (
        ParameterSpec("amount", "Amount", PortType.FLOAT, 1.0),
        ParameterSpec("sigma", "Sigma", PortType.FLOAT, 1.0, minimum=0.0),
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
    )


__all__ = ["create_filter_definitions"]
