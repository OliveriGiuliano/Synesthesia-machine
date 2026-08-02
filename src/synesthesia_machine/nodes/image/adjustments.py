"""Phase 5 image-adjustment node definitions and thin runtime adapters."""

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
    ChannelSelection,
    ConstantChannelPolicy,
    NearZeroPolicy,
    StretchMode,
    add_scalar_image,
    brightness_image,
    clamp_image,
    colour_levels_image,
    contrast_image,
    divide_scalar_image,
    gamma_image,
    hue_image,
    invert_colour_image,
    multiply_scalar_image,
    opacity_image,
    saturation_image,
    stretch_contrast_image,
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
    number_value,
    text_value,
)

type AdjustmentProcessor = Callable[
    [ImageFrame, Mapping[str, RuntimeValue], Mapping[str, ParameterValue]], ImageFrame
]


class AdjustmentRuntime(StatelessImageRuntime):
    def __init__(
        self,
        node_id: UUID,
        processor: AdjustmentProcessor,
        error_code: str,
    ) -> None:
        super().__init__(node_id)
        self._processor = processor
        self._error_code = error_code

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            result = self._processor(image_value(inputs["image"]), inputs, parameters)
        except ValueError as error:
            raise ExpectedNodeError(self._error_code, str(error)) from error
        return {"image": result}


def _factory(processor: AdjustmentProcessor, error_code: str) -> Callable[[UUID], NodeRuntime]:
    def create(node_id: UUID) -> NodeRuntime:
        return AdjustmentRuntime(node_id, processor, error_code)

    return create


def _number(
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    parameter_id: str,
) -> float:
    return number_value(inputs.get(parameter_id, parameters[parameter_id]))


def _selection(parameters: Mapping[str, ParameterValue]) -> ChannelSelection:
    return ChannelSelection(text_value(parameters["channels"]))


def _brightness(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return brightness_image(image, _number(inputs, parameters, "offset"), _selection(parameters))


def _contrast(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return contrast_image(
        image,
        _number(inputs, parameters, "factor"),
        _number(inputs, parameters, "pivot"),
        _selection(parameters),
    )


def _clamp(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return clamp_image(
        image,
        _number(inputs, parameters, "minimum"),
        _number(inputs, parameters, "maximum"),
        _selection(parameters),
        include_alpha=boolean_value(parameters["include_alpha"]),
    )


def _colour_levels(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return colour_levels_image(
        image,
        input_black=_number(inputs, parameters, "input_black"),
        input_white=_number(inputs, parameters, "input_white"),
        gamma=_number(inputs, parameters, "gamma"),
        output_black=_number(inputs, parameters, "output_black"),
        output_white=_number(inputs, parameters, "output_white"),
        selection=_selection(parameters),
    )


def _hue(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return hue_image(image, _number(inputs, parameters, "turns"))


def _saturation(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return saturation_image(image, _number(inputs, parameters, "factor"))


def _invert(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    del inputs
    return invert_colour_image(image, invert_alpha=boolean_value(parameters["invert_alpha"]))


def _opacity(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return opacity_image(image, _number(inputs, parameters, "factor"))


def _stretch(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    del inputs
    return stretch_contrast_image(
        image,
        mode=StretchMode(text_value(parameters["mode"])),
        lower_percentile=number_value(parameters["lower_percentile"]),
        upper_percentile=number_value(parameters["upper_percentile"]),
        ignore_non_finite=boolean_value(parameters["ignore_non_finite"]),
        constant_policy=ConstantChannelPolicy(text_value(parameters["constant_policy"])),
        selection=_selection(parameters),
    )


def _gamma(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return gamma_image(image, _number(inputs, parameters, "gamma"), _selection(parameters))


def _add_scalar(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return add_scalar_image(image, _number(inputs, parameters, "value"), _selection(parameters))


def _multiply_scalar(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return multiply_scalar_image(
        image, _number(inputs, parameters, "value"), _selection(parameters)
    )


def _divide_scalar(
    image: ImageFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame:
    return divide_scalar_image(
        image,
        _number(inputs, parameters, "value"),
        _selection(parameters),
        near_zero_policy=NearZeroPolicy(text_value(parameters["near_zero_policy"])),
        epsilon=number_value(parameters["epsilon"]),
    )


def _channel_parameter() -> ParameterSpec:
    return ParameterSpec(
        "channels",
        "Channels",
        PortType.STRING,
        ChannelSelection.COLOUR.value,
        choices=tuple(selection.value for selection in ChannelSelection),
    )


def _float_parameter(
    parameter_id: str,
    label: str,
    default: float,
    *,
    connectable: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
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
    )


def _validate_levels(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors: list[str] = []
    input_black = number_value(parameters["input_black"])
    input_white = number_value(parameters["input_white"])
    gamma = number_value(parameters["gamma"])
    output_black = number_value(parameters["output_black"])
    output_white = number_value(parameters["output_white"])
    if not all(
        math.isfinite(value)
        for value in (input_black, input_white, gamma, output_black, output_white)
    ):
        errors.append("colour levels bounds and gamma must be finite")
    elif input_white <= input_black:
        errors.append("input white must be greater than input black")
    if math.isfinite(gamma) and gamma <= 0.0:
        errors.append("gamma must be positive")
    return errors


def _validate_clamp(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    minimum = number_value(parameters["minimum"])
    maximum = number_value(parameters["maximum"])
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return ("clamp bounds must be finite",)
    if maximum < minimum:
        return ("maximum must be greater than or equal to minimum",)
    return ()


def _validate_stretch(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    lower = number_value(parameters["lower_percentile"])
    upper = number_value(parameters["upper_percentile"])
    if not math.isfinite(lower) or not math.isfinite(upper):
        return ("stretch percentiles must be finite",)
    if not 0.0 <= lower < upper <= 100.0:
        return ("percentiles must satisfy 0 <= lower < upper <= 100",)
    return ()


def _validate_positive(
    parameter_id: str,
) -> Callable[[Mapping[str, ParameterValue]], Sequence[str]]:
    def validate(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
        value = number_value(parameters[parameter_id])
        if not math.isfinite(value) or value <= 0.0:
            return (f"{parameter_id} must be finite and positive",)
        return ()

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
    processor: AdjustmentProcessor,
    *,
    aliases: tuple[str, ...] = (),
    validator: Callable[[Mapping[str, ParameterValue]], Sequence[str]] | None = None,
) -> NodeDefinition:
    return NodeDefinition(
        type_id,
        1,
        display_name,
        "Image / Adjustment",
        description,
        (InputPortSpec("image", "Image", PortType.IMAGE),),
        (OutputPortSpec("image", "Image", PortType.IMAGE),),
        parameters,
        ExecutionKind.STATELESS,
        _factory(processor, f"invalid_{type_id.rsplit('.', 1)[1]}"),
        aliases=aliases,
        parameter_validator=_combined_validator(parameters, validator),
    )


def create_adjustment_definitions() -> tuple[NodeDefinition, ...]:
    return (
        _definition(
            "synmachine.image.brightness",
            "Brightness",
            "Add an unclipped offset to selected channels while preserving alpha by default.",
            (_float_parameter("offset", "Offset", 0.0), _channel_parameter()),
            _brightness,
            aliases=("exposure offset", "lighten", "darken"),
        ),
        _definition(
            "synmachine.image.contrast",
            "Contrast",
            "Scale selected channels around a configurable pivot without clipping.",
            (
                _float_parameter("factor", "Factor", 1.0),
                _float_parameter("pivot", "Pivot", 0.5),
                _channel_parameter(),
            ),
            _contrast,
        ),
        _definition(
            "synmachine.image.clamp",
            "Clamp",
            "Clamp selected channels to explicit finite bounds.",
            (
                _float_parameter("minimum", "Minimum", 0.0),
                _float_parameter("maximum", "Maximum", 1.0),
                _channel_parameter(),
                ParameterSpec("include_alpha", "Include alpha", PortType.BOOL, False),
            ),
            _clamp,
            validator=_validate_clamp,
        ),
        _definition(
            "synmachine.image.colour_levels",
            "Colour Levels",
            "Map selected input levels through gamma to explicit output levels.",
            (
                _float_parameter("input_black", "Input black", 0.0),
                _float_parameter("input_white", "Input white", 1.0),
                _float_parameter("gamma", "Gamma", 1.0),
                _float_parameter("output_black", "Output black", 0.0),
                _float_parameter("output_white", "Output white", 1.0),
                _channel_parameter(),
            ),
            _colour_levels,
            aliases=("levels", "black point", "white point"),
            validator=_validate_levels,
        ),
        _definition(
            "synmachine.image.hue",
            "Hue",
            "Rotate hue by normalized turns while preserving the original descriptor and alpha.",
            (_float_parameter("turns", "Turns", 0.0),),
            _hue,
            aliases=("hue shift", "colour rotate"),
        ),
        _definition(
            "synmachine.image.saturation",
            "Saturation",
            "Multiply colour saturation through descriptor-aware conversion.",
            (_float_parameter("factor", "Factor", 1.0, minimum=0.0),),
            _saturation,
        ),
        _definition(
            "synmachine.image.invert_colour",
            "Invert Colour",
            "Invert normalized colour channels with optional alpha inversion.",
            (ParameterSpec("invert_alpha", "Invert alpha", PortType.BOOL, False),),
            _invert,
            aliases=("negative", "invert color"),
        ),
        _definition(
            "synmachine.image.opacity",
            "Opacity",
            "Ensure straight RGBA output and multiply alpha by a non-negative factor.",
            (_float_parameter("factor", "Factor", 1.0, minimum=0.0),),
            _opacity,
            aliases=("alpha", "transparency"),
        ),
        _definition(
            "synmachine.image.stretch_contrast",
            "Stretch Contrast",
            "Map percentile bounds to 0..1 per channel or across selected channels.",
            (
                ParameterSpec(
                    "mode",
                    "Mode",
                    PortType.STRING,
                    StretchMode.PER_CHANNEL.value,
                    choices=tuple(mode.value for mode in StretchMode),
                ),
                _float_parameter("lower_percentile", "Lower percentile", 0.0, connectable=False),
                _float_parameter("upper_percentile", "Upper percentile", 100.0, connectable=False),
                ParameterSpec("ignore_non_finite", "Ignore non-finite", PortType.BOOL, True),
                ParameterSpec(
                    "constant_policy",
                    "Constant channel",
                    PortType.STRING,
                    ConstantChannelPolicy.PRESERVE.value,
                    choices=tuple(policy.value for policy in ConstantChannelPolicy),
                ),
                _channel_parameter(),
            ),
            _stretch,
            aliases=("normalize", "auto levels", "dynamic range"),
            validator=_validate_stretch,
        ),
        _definition(
            "synmachine.image.gamma",
            "Gamma",
            "Apply max(x, 0) raised to a positive gamma on selected channels.",
            (_float_parameter("gamma", "Gamma", 1.0), _channel_parameter()),
            _gamma,
            validator=_validate_positive("gamma"),
        ),
        _definition(
            "synmachine.image.add_scalar",
            "Image Add Scalar",
            "Add one connectable scalar to selected image channels.",
            (_float_parameter("value", "Value", 0.0), _channel_parameter()),
            _add_scalar,
            aliases=("image offset",),
        ),
        _definition(
            "synmachine.image.multiply_scalar",
            "Image Multiply Scalar",
            "Multiply selected image channels by one connectable scalar.",
            (_float_parameter("value", "Value", 1.0), _channel_parameter()),
            _multiply_scalar,
            aliases=("image scale",),
        ),
        _definition(
            "synmachine.image.divide_scalar",
            "Image Divide Scalar",
            "Divide selected image channels using an explicit near-zero policy.",
            (
                _float_parameter("value", "Value", 1.0),
                ParameterSpec(
                    "near_zero_policy",
                    "Near-zero policy",
                    PortType.STRING,
                    NearZeroPolicy.REPLACE_WITH_ZERO.value,
                    choices=tuple(policy.value for policy in NearZeroPolicy),
                ),
                _float_parameter("epsilon", "Epsilon", 1e-6, connectable=False),
                _channel_parameter(),
            ),
            _divide_scalar,
            aliases=("image ratio",),
            validator=_validate_positive("epsilon"),
        ),
    )


__all__ = ["create_adjustment_definitions"]
