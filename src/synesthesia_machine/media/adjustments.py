"""Vectorized immutable image adjustment algorithms."""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelSemantic,
    ColorSpace,
    ImageFrame,
    read_only_float32,
)
from synesthesia_machine.media.colour import color_space_descriptor, convert_image
from synesthesia_machine.media.image_common import (
    ChannelSelection,
    frame_like,
    recombine_alpha,
    selected_channel_indices,
    split_alpha,
)


class StretchMode(StrEnum):
    PER_CHANNEL = "PER_CHANNEL"
    COMBINED = "COMBINED"


class ConstantChannelPolicy(StrEnum):
    PRESERVE = "PRESERVE"
    ZERO = "ZERO"
    MIDPOINT = "MIDPOINT"


class NearZeroPolicy(StrEnum):
    REPLACE_WITH_ZERO = "REPLACE_WITH_ZERO"
    CLAMP_EPSILON = "CLAMP_EPSILON"
    ERROR = "ERROR"


def brightness_image(image: ImageFrame, offset: float, selection: ChannelSelection) -> ImageFrame:
    _require_finite(offset, "brightness offset")
    return _apply_selected(image, selection, lambda values: values + np.float32(offset))


def contrast_image(
    image: ImageFrame,
    factor: float,
    pivot: float,
    selection: ChannelSelection,
) -> ImageFrame:
    _require_finite(factor, "contrast factor")
    _require_finite(pivot, "contrast pivot")
    factor32 = np.float32(factor)
    pivot32 = np.float32(pivot)
    return _apply_selected(
        image,
        selection,
        lambda values: (values - pivot32) * factor32 + pivot32,
    )


def clamp_image(
    image: ImageFrame,
    minimum: float,
    maximum: float,
    selection: ChannelSelection,
    *,
    include_alpha: bool,
) -> ImageFrame:
    _require_finite(minimum, "clamp minimum")
    _require_finite(maximum, "clamp maximum")
    if maximum < minimum:
        raise ValueError("clamp maximum must be greater than or equal to minimum")
    return _apply_selected(
        image,
        selection,
        lambda values: np.clip(values, minimum, maximum),
        include_alpha=include_alpha,
    )


def colour_levels_image(
    image: ImageFrame,
    *,
    input_black: float,
    input_white: float,
    gamma: float,
    output_black: float,
    output_white: float,
    selection: ChannelSelection,
) -> ImageFrame:
    for value, name in (
        (input_black, "input black"),
        (input_white, "input white"),
        (gamma, "gamma"),
        (output_black, "output black"),
        (output_white, "output white"),
    ):
        _require_finite(value, f"colour levels {name}")
    if input_white <= input_black:
        raise ValueError("colour levels input white must be greater than input black")
    if gamma <= 0.0:
        raise ValueError("colour levels gamma must be positive")

    def transform(values: NDArray[np.float32]) -> NDArray[np.float32]:
        normalized = (values - np.float32(input_black)) / np.float32(input_white - input_black)
        with np.errstate(invalid="ignore", over="ignore"):
            corrected = np.power(normalized, np.float32(1.0 / gamma))
        return corrected * np.float32(output_white - output_black) + np.float32(output_black)

    return _apply_selected(image, selection, transform)


def hue_image(image: ImageFrame, turns: float) -> ImageFrame:
    _require_finite(turns, "hue turns")
    _require_finite_colour(image, "Hue")
    if image.color_space in (ColorSpace.HSV, ColorSpace.HSL):
        result = np.array(image.data, dtype=np.float32, order="C", copy=True)
        result[..., 0] = np.mod(result[..., 0] + np.float32(turns), np.float32(1.0))
        return frame_like(image, result)
    hsv, alpha = _image_as_hsv(image)
    adjusted = np.array(hsv.data, dtype=np.float32, order="C", copy=True)
    adjusted[..., 0] = np.mod(adjusted[..., 0] + np.float32(turns), np.float32(1.0))
    return _hsv_to_original(image, frame_like(hsv, adjusted), alpha)


def saturation_image(image: ImageFrame, factor: float) -> ImageFrame:
    _require_finite(factor, "saturation factor")
    if factor < 0.0:
        raise ValueError("saturation factor must be non-negative")
    _require_finite_colour(image, "Saturation")
    if image.color_space in (ColorSpace.HSV, ColorSpace.HSL):
        result = np.array(image.data, dtype=np.float32, order="C", copy=True)
        result[..., 1] *= np.float32(factor)
        return frame_like(image, result)
    hsv, alpha = _image_as_hsv(image)
    adjusted = np.array(hsv.data, dtype=np.float32, order="C", copy=True)
    adjusted[..., 1] *= np.float32(factor)
    return _hsv_to_original(image, frame_like(hsv, adjusted), alpha)


def invert_colour_image(image: ImageFrame, *, invert_alpha: bool) -> ImageFrame:
    descriptor = color_space_descriptor(image.color_space)
    if any(
        channel.nominal_min != 0.0 or channel.nominal_max != 1.0
        for channel in descriptor.channels
        if channel.semantic is not ChannelSemantic.ALPHA
    ):
        raise ValueError("Invert Colour requires normalized 0..1 colour channels")
    return _apply_selected(
        image,
        ChannelSelection.COLOUR,
        lambda values: np.float32(1.0) - values,
        include_alpha=invert_alpha,
    )


def opacity_image(image: ImageFrame, factor: float) -> ImageFrame:
    _require_finite(factor, "opacity factor")
    if factor < 0.0:
        raise ValueError("opacity factor must be non-negative")
    rgba = image if image.color_space is ColorSpace.RGBA else convert_image(image, ColorSpace.RGBA)
    result = np.array(rgba.data, dtype=np.float32, order="C", copy=True)
    result[..., 3] *= np.float32(factor)
    return frame_like(rgba, result)


def stretch_contrast_image(
    image: ImageFrame,
    *,
    mode: StretchMode,
    lower_percentile: float,
    upper_percentile: float,
    ignore_non_finite: bool,
    constant_policy: ConstantChannelPolicy,
    selection: ChannelSelection,
) -> ImageFrame:
    _validate_percentiles(lower_percentile, upper_percentile)
    indices = selected_channel_indices(image.color_space, selection)
    result = np.array(image.data, dtype=np.float32, order="C", copy=True)
    if mode is StretchMode.COMBINED:
        selected = result[..., indices]
        lower, upper = _percentile_bounds(
            selected, lower_percentile, upper_percentile, ignore_non_finite
        )
        result[..., indices] = _stretch_values(selected, lower, upper, constant_policy)
    else:
        for index in indices:
            values = result[..., index]
            lower, upper = _percentile_bounds(
                values, lower_percentile, upper_percentile, ignore_non_finite
            )
            result[..., index] = _stretch_values(values, lower, upper, constant_policy)
    return frame_like(image, result)


def gamma_image(image: ImageFrame, gamma: float, selection: ChannelSelection) -> ImageFrame:
    _require_finite(gamma, "gamma")
    if gamma <= 0.0:
        raise ValueError("gamma must be positive")

    def transform(values: NDArray[np.float32]) -> NDArray[np.float32]:
        with np.errstate(invalid="ignore", over="ignore"):
            return np.power(np.maximum(values, np.float32(0.0)), np.float32(gamma))

    return _apply_selected(image, selection, transform)


def add_scalar_image(image: ImageFrame, value: float, selection: ChannelSelection) -> ImageFrame:
    _require_finite(value, "add scalar value")
    return _apply_selected(image, selection, lambda values: values + np.float32(value))


def multiply_scalar_image(
    image: ImageFrame, value: float, selection: ChannelSelection
) -> ImageFrame:
    _require_finite(value, "multiply scalar value")
    return _apply_selected(image, selection, lambda values: values * np.float32(value))


def divide_scalar_image(
    image: ImageFrame,
    value: float,
    selection: ChannelSelection,
    *,
    near_zero_policy: NearZeroPolicy,
    epsilon: float,
) -> ImageFrame:
    _require_finite(value, "divide scalar value")
    _require_finite(epsilon, "divide scalar epsilon")
    if epsilon <= 0.0:
        raise ValueError("divide scalar epsilon must be positive")
    divisor = value
    if abs(divisor) < epsilon:
        if near_zero_policy is NearZeroPolicy.ERROR:
            raise ValueError("divide scalar divisor is within epsilon of zero")
        if near_zero_policy is NearZeroPolicy.REPLACE_WITH_ZERO:
            return _apply_selected(
                image,
                selection,
                lambda values: np.zeros_like(values, dtype=np.float32),
            )
        divisor = math.copysign(epsilon, divisor) if divisor != 0.0 else epsilon
    return _apply_selected(image, selection, lambda values: values / np.float32(divisor))


def _apply_selected(
    image: ImageFrame,
    selection: ChannelSelection,
    transform: Callable[[NDArray[np.float32]], NDArray[np.float32]],
    *,
    include_alpha: bool = False,
) -> ImageFrame:
    indices = selected_channel_indices(image.color_space, selection, include_alpha=include_alpha)
    result = np.array(image.data, dtype=np.float32, order="C", copy=True)
    result[..., indices] = np.asarray(transform(result[..., indices]), dtype=np.float32)
    return frame_like(image, result)


def _image_as_hsv(
    image: ImageFrame,
) -> tuple[ImageFrame, NDArray[np.float32] | None]:
    colour, alpha = split_alpha(image.data, image.color_space)
    if image.color_space is ColorSpace.RGBA:
        descriptor = color_space_descriptor(ColorSpace.SRGB)
        source = ImageFrame(
            read_only_float32(colour),
            ColorSpace.SRGB,
            descriptor.channel_names,
            AlphaMode.NONE,
            image.context,
            image.provenance,
        )
    else:
        source = image
    return convert_image(source, ColorSpace.HSV), alpha


def _hsv_to_original(
    original: ImageFrame,
    hsv: ImageFrame,
    alpha: NDArray[np.float32] | None,
) -> ImageFrame:
    if original.color_space is ColorSpace.RGBA:
        rgb = convert_image(hsv, ColorSpace.SRGB)
        return frame_like(
            original,
            recombine_alpha(rgb.data, alpha, ColorSpace.RGBA),
        )
    converted = convert_image(hsv, original.color_space)
    return frame_like(original, converted.data)


def _require_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _require_finite_colour(image: ImageFrame, node_name: str) -> None:
    colour, _ = split_alpha(image.data, image.color_space)
    if not np.isfinite(colour).all():
        raise ValueError(f"{node_name} requires finite colour-channel values")


def _validate_percentiles(lower: float, upper: float) -> None:
    _require_finite(lower, "lower percentile")
    _require_finite(upper, "upper percentile")
    if not 0.0 <= lower < upper <= 100.0:
        raise ValueError("stretch percentiles must satisfy 0 <= lower < upper <= 100")


def _percentile_bounds(
    values: NDArray[np.float32],
    lower_percentile: float,
    upper_percentile: float,
    ignore_non_finite: bool,
) -> tuple[float, float]:
    samples = values[np.isfinite(values)] if ignore_non_finite else values.reshape(-1)
    if samples.size == 0:
        raise ValueError("stretch contrast has no finite selected samples")
    if not ignore_non_finite and not np.isfinite(samples).all():
        raise ValueError(
            "stretch contrast selected samples must be finite when ignore_non_finite is false"
        )
    lower, upper = np.percentile(samples, [lower_percentile, upper_percentile])
    return float(lower), float(upper)


def _stretch_values(
    values: NDArray[np.float32],
    lower: float,
    upper: float,
    constant_policy: ConstantChannelPolicy,
) -> NDArray[np.float32]:
    finite = np.isfinite(values)
    if upper <= lower:
        if constant_policy is ConstantChannelPolicy.PRESERVE:
            return values
        replacement = (
            np.float32(0.0) if constant_policy is ConstantChannelPolicy.ZERO else np.float32(0.5)
        )
        return np.where(finite, replacement, values).astype(np.float32, copy=False)
    stretched = np.clip((values - np.float32(lower)) / np.float32(upper - lower), 0.0, 1.0)
    return np.where(finite, stretched, values).astype(np.float32, copy=False)


__all__ = [
    "ConstantChannelPolicy",
    "NearZeroPolicy",
    "StretchMode",
    "add_scalar_image",
    "brightness_image",
    "clamp_image",
    "colour_levels_image",
    "contrast_image",
    "divide_scalar_image",
    "gamma_image",
    "hue_image",
    "invert_colour_image",
    "multiply_scalar_image",
    "opacity_image",
    "saturation_image",
    "stretch_contrast_image",
]
