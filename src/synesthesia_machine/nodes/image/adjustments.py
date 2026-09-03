"""Image-adjustment node definitions and thin runtime adapters."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
    ImageFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
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
    ParameterEditorHint,
    ParameterSpec,
    TypeVariable,
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    boolean_value,
    number_value,
    text_value,
)

IMAGE_OR_CHANNEL = TypeVariable("IMAGE_OR_CHANNEL", frozenset({PortType.IMAGE, PortType.CHANNEL}))

type AdjustmentProcessor = Callable[..., ImageFrame | ChannelFrame]


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
            source = inputs["image"]
            if not isinstance(source, (ImageFrame, ChannelFrame)):
                raise TypeError(f"Expected image or channel, got {type(source).__name__}")
            result = self._processor(source, inputs, parameters)
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
    image: ImageFrame | ChannelFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame | ChannelFrame:
    if isinstance(image, ChannelFrame):
        minimum = _number(inputs, parameters, "minimum")
        maximum = _number(inputs, parameters, "maximum")
        if maximum < minimum:
            raise ValueError("clamp maximum must be greater than or equal to minimum")
        return _channel_like(image, np.clip(image.data, minimum, maximum))
    return clamp_image(
        image,
        _number(inputs, parameters, "minimum"),
        _number(inputs, parameters, "maximum"),
        _selection(parameters),
        include_alpha=False,
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
    del inputs, parameters
    return invert_colour_image(image, invert_alpha=False)


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
    image: ImageFrame | ChannelFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame | ChannelFrame:
    if isinstance(image, ChannelFrame):
        return _channel_like(image, image.data + np.float32(_number(inputs, parameters, "value")))
    return add_scalar_image(image, _number(inputs, parameters, "value"), _selection(parameters))


def _multiply_scalar(
    image: ImageFrame | ChannelFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame | ChannelFrame:
    if isinstance(image, ChannelFrame):
        return _channel_like(image, image.data * np.float32(_number(inputs, parameters, "value")))
    return multiply_scalar_image(
        image, _number(inputs, parameters, "value"), _selection(parameters)
    )


def _divide_scalar(
    image: ImageFrame | ChannelFrame,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
) -> ImageFrame | ChannelFrame:
    if isinstance(image, ChannelFrame):
        denominator = _number(inputs, parameters, "value")
        epsilon = number_value(parameters["epsilon"])
        policy = NearZeroPolicy(text_value(parameters["near_zero_policy"]))
        if abs(denominator) < epsilon:
            if policy is NearZeroPolicy.ERROR:
                raise ValueError("division scalar is near zero")
            if policy is NearZeroPolicy.REPLACE_WITH_ZERO:
                return _channel_like(image, np.zeros_like(image.data))
            denominator = math.copysign(epsilon, denominator if denominator else 1.0)
        return _channel_like(image, image.data / np.float32(denominator))
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
        # Sources never carry a fourth (alpha) channel, so CHANNEL_4 is not
        # offered as a selectable target.
        choices=tuple(
            selection.value
            for selection in ChannelSelection
            if selection is not ChannelSelection.CHANNEL_4
        ),
        applicable_input_types=(PortType.IMAGE,),
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
    dynamic: bool = False,
    implementation_version: int = 1,
) -> NodeDefinition:
    return NodeDefinition(
        type_id,
        implementation_version,
        display_name,
        "Image / Adjustment",
        description,
        (InputPortSpec("image", "Image / Channel" if dynamic else "Image", PortType.IMAGE),),
        (OutputPortSpec("image", "Image / Channel" if dynamic else "Image", PortType.IMAGE),),
        parameters,
        ExecutionKind.STATELESS,
        _factory(processor, f"invalid_{type_id.rsplit('.', 1)[1]}"),
        aliases=aliases,
        parameter_validator=_combined_validator(parameters, validator),
        port_type_resolver=_dynamic_image_channel_type if dynamic else None,
    )


def _dynamic_image_channel_type(
    port_id: str, is_output: bool, parameters: Mapping[str, ParameterValue]
) -> TypeVariable:
    del port_id, is_output, parameters
    return IMAGE_OR_CHANNEL


def create_adjustment_definitions() -> tuple[NodeDefinition, ...]:
    return (
        _definition(
            "synmachine.image.brightness",
            "Brightness",
            "Makes the image brighter or darker. A positive offset lightens it, a negative one "
            "darkens it.",
            (_float_parameter("offset", "Offset", 0.0), _channel_parameter()),
            _brightness,
            aliases=("exposure offset", "lighten", "darken"),
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.contrast",
            "Contrast",
            "Strengthens or softens the differences between the light and dark parts of the image.",
            (
                _float_parameter("factor", "Factor", 1.0),
                _float_parameter("pivot", "Pivot", 0.5),
                _channel_parameter(),
            ),
            _contrast,
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.clamp",
            "Clamp",
            "Keeps the values inside the minimum and maximum you set. Anything above or below is "
            "brought back to the limits.",
            (
                _float_parameter("minimum", "Minimum", 0.0),
                _float_parameter("maximum", "Maximum", 1.0),
                _channel_parameter(),
            ),
            _clamp,
            validator=_validate_clamp,
            dynamic=True,
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.colour_levels",
            "Colour Levels",
            "Rebalances the dark and light parts of the image, like the levels control in a photo "
            "editor.",
            (
                _float_parameter("input_black", "Input black", 0.0),
                _float_parameter("input_white", "Input white", 1.0),
                _float_parameter("gamma", "Gamma", 1.0, minimum=1e-6),
                _float_parameter("output_black", "Output black", 0.0),
                _float_parameter("output_white", "Output white", 1.0),
                _channel_parameter(),
            ),
            _colour_levels,
            aliases=("levels", "black point", "white point"),
            validator=_validate_levels,
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.hue",
            "Hue",
            "Shifts the colours around the colour wheel, for example turning reds toward magentas.",
            (
                ParameterSpec(
                    "turns",
                    "Turns",
                    PortType.FLOAT,
                    0.0,
                    minimum=0.0,
                    maximum=1.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
            ),
            _hue,
            aliases=("hue shift", "colour rotate"),
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.saturation",
            "Saturation",
            "Makes the colours more or less intense. Below 1 the image fades toward grey, above 1 "
            "the colours stand out more.",
            (_float_parameter("factor", "Factor", 1.0, minimum=0.0),),
            _saturation,
        ),
        _definition(
            "synmachine.image.invert_colour",
            "Invert Colour",
            "Flips the image like a photograph negative: dark becomes light and light "
            "becomes dark.",
            (),
            _invert,
            aliases=("negative", "invert color"),
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.stretch_contrast",
            "Stretch Contrast",
            "Spreads the image's tones across the full brightness range so washed-out pictures "
            "gain contrast.",
            (
                ParameterSpec(
                    "mode",
                    "Mode",
                    PortType.STRING,
                    StretchMode.PER_CHANNEL.value,
                    choices=tuple(mode.value for mode in StretchMode),
                ),
                _float_parameter(
                    "lower_percentile", "Lower percentile", 0.0, minimum=0.0, maximum=100.0
                ),
                _float_parameter(
                    "upper_percentile", "Upper percentile", 100.0, minimum=0.0, maximum=100.0
                ),
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
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.gamma",
            "Gamma",
            "Brightens or darkens the middle tones of the image without touching the pure blacks "
            "and whites.",
            (
                _float_parameter("gamma", "Gamma", 1.0, minimum=1e-6),
                _channel_parameter(),
            ),
            _gamma,
            validator=_validate_positive("gamma"),
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.add_scalar",
            "Image Add Scalar",
            "Adds a fixed amount to the chosen channels. Positive values brighten the image, "
            "negative values darken it.",
            (_float_parameter("value", "Value", 0.0), _channel_parameter()),
            _add_scalar,
            aliases=("image offset",),
            dynamic=True,
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.multiply_scalar",
            "Image Multiply Scalar",
            "Multiplies the chosen channels by a value. Above 1 the image brightens, below 1 it "
            "darkens.",
            (_float_parameter("value", "Value", 1.0), _channel_parameter()),
            _multiply_scalar,
            aliases=("image scale",),
            dynamic=True,
            implementation_version=2,
        ),
        _definition(
            "synmachine.image.divide_scalar",
            "Image Divide Scalar",
            "Divides the chosen channels by a value, with a safety setting for values close to "
            "zero.",
            (
                _float_parameter("value", "Value", 1.0),
                ParameterSpec(
                    "near_zero_policy",
                    "Near-zero policy",
                    PortType.STRING,
                    NearZeroPolicy.REPLACE_WITH_ZERO.value,
                    choices=tuple(policy.value for policy in NearZeroPolicy),
                ),
                _float_parameter("epsilon", "Epsilon", 1e-6, minimum=1e-12),
                _channel_parameter(),
            ),
            _divide_scalar,
            aliases=("image ratio",),
            validator=_validate_positive("epsilon"),
            dynamic=True,
            implementation_version=2,
        ),
    )


def _channel_like(channel: ChannelFrame, data: np.ndarray) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(data, dtype=np.float32)),
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )


__all__ = ["create_adjustment_definitions"]
