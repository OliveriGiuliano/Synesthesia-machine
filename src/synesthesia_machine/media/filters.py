"""Vectorized immutable image-filter algorithms."""

from __future__ import annotations

import math
from enum import StrEnum

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    ImageFrame,
    NumericMatrix,
    read_only_float32,
)
from synesthesia_machine.media.colour import image_to_luminance
from synesthesia_machine.media.image_common import (
    BorderMode,
    ChannelSelection,
    cv_border_mode,
    frame_like,
    recombine_alpha,
    selected_channel_indices,
    split_alpha,
    validate_odd_kernel,
)


class NoiseType(StrEnum):
    GAUSSIAN = "GAUSSIAN"
    UNIFORM = "UNIFORM"
    SALT_AND_PEPPER = "SALT_AND_PEPPER"


class ThresholdMode(StrEnum):
    BINARY = "BINARY"
    BINARY_INVERSE = "BINARY_INVERSE"
    TRUNCATE = "TRUNCATE"
    TO_ZERO = "TO_ZERO"
    TO_ZERO_INVERSE = "TO_ZERO_INVERSE"


class ConvolutionNormalization(StrEnum):
    NONE = "NONE"
    SUM_TO_ONE = "SUM_TO_ONE"
    ABSOLUTE_SUM_TO_ONE = "ABSOLUTE_SUM_TO_ONE"


class MorphKernelShape(StrEnum):
    RECTANGLE = "RECTANGLE"
    ELLIPSE = "ELLIPSE"
    CROSS = "CROSS"


_CV_THRESHOLD = {
    ThresholdMode.BINARY: cv2.THRESH_BINARY,
    ThresholdMode.BINARY_INVERSE: cv2.THRESH_BINARY_INV,
    ThresholdMode.TRUNCATE: cv2.THRESH_TRUNC,
    ThresholdMode.TO_ZERO: cv2.THRESH_TOZERO,
    ThresholdMode.TO_ZERO_INVERSE: cv2.THRESH_TOZERO_INV,
}

_CV_MORPH_SHAPE = {
    MorphKernelShape.RECTANGLE: cv2.MORPH_RECT,
    MorphKernelShape.ELLIPSE: cv2.MORPH_ELLIPSE,
    MorphKernelShape.CROSS: cv2.MORPH_CROSS,
}


def gaussian_blur_image(
    image: ImageFrame,
    *,
    kernel_width: int,
    kernel_height: int,
    sigma_x: float,
    sigma_y: float,
    border_mode: BorderMode,
) -> ImageFrame:
    """Blur descriptor-declared colour channels while preserving alpha."""

    validate_odd_kernel(kernel_width, kernel_height)
    _require_non_negative_finite(sigma_x, "Gaussian blur sigma X")
    _require_non_negative_finite(sigma_y, "Gaussian blur sigma Y")
    colour, alpha = split_alpha(image.data, image.color_space)
    blurred = _gaussian_blur_values(
        colour,
        kernel_size=(kernel_width, kernel_height),
        sigma_x=sigma_x,
        sigma_y=sigma_y,
        border_mode=border_mode,
        wrap_padding=(kernel_width // 2, kernel_height // 2),
    )
    return frame_like(image, recombine_alpha(blurred, alpha, image.color_space))


def sharpen_image(
    image: ImageFrame,
    *,
    amount: float,
    sigma: float,
    threshold: float,
    border_mode: BorderMode,
) -> ImageFrame:
    """Apply unclipped unsharp masking to non-alpha channels."""

    _require_finite(amount, "Sharpen amount")
    _require_positive_finite(sigma, "Sharpen sigma")
    _require_non_negative_finite(threshold, "Sharpen threshold")
    colour, alpha = split_alpha(image.data, image.color_space)
    blur = _gaussian_blur_values(
        colour,
        kernel_size=(0, 0),
        sigma_x=sigma,
        sigma_y=sigma,
        border_mode=border_mode,
        # OpenCV's automatic float32 Gaussian kernel has a radius of at most 4 sigma.
        wrap_padding=(math.ceil(4.0 * sigma), math.ceil(4.0 * sigma)),
    )
    detail = colour - blur
    if threshold > 0.0:
        detail = np.where(np.abs(detail) >= np.float32(threshold), detail, np.float32(0.0))
    sharpened = colour + np.float32(amount) * detail
    return frame_like(image, recombine_alpha(sharpened, alpha, image.color_space))


def add_noise_image(
    image: ImageFrame,
    *,
    noise_type: NoiseType,
    amount: float,
    seed: int,
    monochrome: bool,
    animate_seed: bool,
    tick_index: int,
    selection: ChannelSelection,
) -> ImageFrame:
    """Add deterministic stateless noise to selected channels without clipping."""

    _require_non_negative_finite(amount, "Noise amount")
    if noise_type is NoiseType.SALT_AND_PEPPER and amount > 1.0:
        raise ValueError("Salt and Pepper noise amount must not exceed 1")
    if isinstance(seed, bool):
        raise ValueError("Noise seed must be an integer")
    if isinstance(tick_index, bool) or tick_index < 1:
        raise ValueError("Noise tick index must be a positive integer")

    indices = selected_channel_indices(image.color_space, selection)
    result = np.array(image.data, dtype=np.float32, order="C", copy=True)
    selected = result[..., indices]
    random_shape = (*selected.shape[:2], 1 if monochrome else selected.shape[2])
    rng = np.random.default_rng(_noise_seed(seed, tick_index, animate_seed=animate_seed))

    if noise_type is NoiseType.GAUSSIAN:
        noise = rng.standard_normal(random_shape, dtype=np.float32) * np.float32(amount)
        transformed = selected + noise
    elif noise_type is NoiseType.UNIFORM:
        noise = rng.random(random_shape, dtype=np.float32)
        noise = (noise * np.float32(2.0) - np.float32(1.0)) * np.float32(amount)
        transformed = selected + noise
    else:
        draws = rng.random(random_shape, dtype=np.float32)
        pepper = draws < np.float32(amount * 0.5)
        salt = (draws >= np.float32(amount * 0.5)) & (draws < np.float32(amount))
        transformed = np.where(pepper, np.float32(0.0), selected)
        transformed = np.where(salt, np.float32(1.0), transformed)
        transformed = np.where(np.isfinite(selected), transformed, selected)

    result[..., indices] = np.asarray(transformed, dtype=np.float32)
    return frame_like(image, result)


def posterize_image(
    image: ImageFrame,
    *,
    levels: int,
    clamp_input: bool,
    selection: ChannelSelection,
) -> ImageFrame:
    """Quantize selected normalized channels to a fixed number of levels."""

    if isinstance(levels, bool) or levels < 2:
        raise ValueError("Posterize levels must be an integer of at least 2")
    indices = selected_channel_indices(image.color_space, selection)
    result = np.array(image.data, dtype=np.float32, order="C", copy=True)
    selected = result[..., indices]
    if clamp_input:
        selected = np.clip(selected, np.float32(0.0), np.float32(1.0))
    steps = np.float32(levels - 1)
    with np.errstate(invalid="ignore", over="ignore"):
        quantized = np.rint(selected * steps) / steps
    result[..., indices] = np.asarray(quantized, dtype=np.float32)
    return frame_like(image, result)


def threshold_channel(
    channel: ChannelFrame,
    *,
    mode: ThresholdMode,
    threshold: float,
    maximum: float,
) -> ChannelFrame:
    """Apply OpenCV-compatible threshold semantics while preserving channel metadata."""

    _require_finite(threshold, "Threshold")
    _require_finite(maximum, "Threshold maximum")
    try:
        _, transformed = cv2.threshold(channel.data, threshold, maximum, _CV_THRESHOLD[mode])
    except cv2.error as error:
        raise ValueError(f"Threshold failed: {error}") from error
    return ChannelFrame(
        read_only_float32(np.asarray(transformed, dtype=np.float32)),
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )


def canny_image(
    image: ImageFrame,
    *,
    low_threshold: float,
    high_threshold: float,
    aperture_size: int,
    l2_gradient: bool,
    pre_blur_sigma: float,
) -> ChannelFrame:
    """Convert an image to luminance and emit a normalized immutable Canny edge mask."""

    colour, _ = split_alpha(image.data, image.color_space)
    if not np.all(np.isfinite(colour)):
        raise ValueError("Canny input must contain only finite values")
    _require_normalized_threshold(low_threshold, "Canny low threshold")
    _require_normalized_threshold(high_threshold, "Canny high threshold")
    if high_threshold < low_threshold:
        raise ValueError("Canny high threshold must be greater than or equal to low threshold")
    if aperture_size not in (3, 5, 7):
        raise ValueError("Canny aperture size must be 3, 5, or 7")
    _require_non_negative_finite(pre_blur_sigma, "Canny pre-blur sigma")

    luminance = image_to_luminance(image)
    values = luminance.data
    if pre_blur_sigma > 0.0:
        values = _gaussian_blur_values(
            values[..., None],
            kernel_size=(0, 0),
            sigma_x=pre_blur_sigma,
            sigma_y=pre_blur_sigma,
            border_mode=BorderMode.REFLECT_101,
            wrap_padding=(0, 0),
        )[..., 0]
    source = np.rint(np.clip(values, 0.0, 1.0) * np.float32(255.0)).astype(np.uint8)
    try:
        edges = cv2.Canny(
            source,
            low_threshold * 255.0,
            high_threshold * 255.0,
            apertureSize=aperture_size,
            L2gradient=l2_gradient,
        )
    except cv2.error as error:
        raise ValueError(f"Canny edge detection failed: {error}") from error
    normalized = np.asarray(edges, dtype=np.float32) / np.float32(255.0)
    return ChannelFrame(
        read_only_float32(normalized),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        image.context,
    )


def convolve_image(
    image: ImageFrame,
    *,
    kernel: NumericMatrix,
    normalization: ConvolutionNormalization,
    scale: float,
    delta: float,
    border_mode: BorderMode,
) -> ImageFrame:
    """Filter non-alpha channels with a bounded persisted numeric kernel."""

    validate_odd_kernel(kernel.width, kernel.height, maximum=15)
    _require_finite(scale, "Convolution scale")
    _require_finite(delta, "Convolution delta")
    kernel_values = np.asarray(kernel.rows, dtype=np.float64)
    normalized_kernel = _normalized_kernel(kernel_values, normalization)
    with np.errstate(invalid="ignore", over="ignore"):
        effective_kernel = normalized_kernel * scale
    float32_max = np.finfo(np.float32).max
    if not np.all(np.isfinite(effective_kernel)) or np.any(np.abs(effective_kernel) > float32_max):
        raise ValueError("Convolution kernel and scale must produce finite float32 coefficients")
    colour, alpha = split_alpha(image.data, image.color_space)
    filtered = _filter_2d_values(
        colour,
        kernel=np.asarray(effective_kernel, dtype=np.float32),
        delta=delta,
        border_mode=border_mode,
    )
    return frame_like(image, recombine_alpha(filtered, alpha, image.color_space))


def dilate_image(
    image: ImageFrame,
    *,
    kernel_shape: MorphKernelShape,
    kernel_width: int,
    kernel_height: int,
    iterations: int,
    anchor_x: int,
    anchor_y: int,
    border_mode: BorderMode,
    process_alpha: bool,
) -> ImageFrame:
    """Apply channel-wise native maximum morphology."""

    return _morph_image(
        image,
        operation="dilate",
        kernel_shape=kernel_shape,
        kernel_width=kernel_width,
        kernel_height=kernel_height,
        iterations=iterations,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        border_mode=border_mode,
        process_alpha=process_alpha,
    )


def erode_image(
    image: ImageFrame,
    *,
    kernel_shape: MorphKernelShape,
    kernel_width: int,
    kernel_height: int,
    iterations: int,
    anchor_x: int,
    anchor_y: int,
    border_mode: BorderMode,
    process_alpha: bool,
) -> ImageFrame:
    """Apply channel-wise native minimum morphology."""

    return _morph_image(
        image,
        operation="erode",
        kernel_shape=kernel_shape,
        kernel_width=kernel_width,
        kernel_height=kernel_height,
        iterations=iterations,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        border_mode=border_mode,
        process_alpha=process_alpha,
    )


def high_pass_image(
    image: ImageFrame,
    *,
    sigma: float,
    display_offset: float,
    gain: float,
    border_mode: BorderMode,
) -> ImageFrame:
    """Return offset plus gain-scaled detail without clipping."""

    _require_positive_finite(sigma, "High-pass sigma")
    _require_finite(display_offset, "High-pass display offset")
    _require_finite(gain, "High-pass gain")
    colour, alpha = split_alpha(image.data, image.color_space)
    blurred = _automatic_gaussian(colour, sigma, border_mode)
    result = np.float32(display_offset) + np.float32(gain) * (colour - blurred)
    return frame_like(image, recombine_alpha(result, alpha, image.color_space))


def low_pass_image(
    image: ImageFrame,
    *,
    sigma: float,
    border_mode: BorderMode,
) -> ImageFrame:
    """Apply a simplified automatic-kernel Gaussian low-pass filter."""

    _require_positive_finite(sigma, "Low-pass sigma")
    colour, alpha = split_alpha(image.data, image.color_space)
    blurred = _automatic_gaussian(colour, sigma, border_mode)
    return frame_like(image, recombine_alpha(blurred, alpha, image.color_space))


def _automatic_gaussian(
    values: NDArray[np.float32], sigma: float, border_mode: BorderMode
) -> NDArray[np.float32]:
    radius = math.ceil(4.0 * sigma)
    return _gaussian_blur_values(
        values,
        kernel_size=(0, 0),
        sigma_x=sigma,
        sigma_y=sigma,
        border_mode=border_mode,
        wrap_padding=(radius, radius),
    )


def _normalized_kernel(
    kernel: NDArray[np.float64], normalization: ConvolutionNormalization
) -> NDArray[np.float64]:
    if normalization is ConvolutionNormalization.NONE:
        return kernel
    maximum = float(np.max(np.abs(kernel)))
    if maximum == 0.0:
        raise ValueError(f"{normalization.value} requires a non-zero kernel denominator")
    scaled = kernel / maximum
    denominator = (
        float(np.sum(scaled, dtype=np.float64))
        if normalization is ConvolutionNormalization.SUM_TO_ONE
        else float(np.sum(np.abs(scaled), dtype=np.float64))
    )
    if denominator == 0.0:
        raise ValueError(f"{normalization.value} requires a non-zero kernel denominator")
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return np.asarray(scaled / denominator, dtype=np.float64)


def _filter_2d_values(
    values: NDArray[np.float32],
    *,
    kernel: NDArray[np.float32],
    delta: float,
    border_mode: BorderMode,
) -> NDArray[np.float32]:
    radius_x = kernel.shape[1] // 2
    radius_y = kernel.shape[0] // 2
    source, native_border, crop = _prepare_native_filter(
        values,
        border_mode,
        left=radius_x,
        right=radius_x,
        top=radius_y,
        bottom=radius_y,
    )
    try:
        filtered = np.asarray(
            cv2.filter2D(source, -1, kernel, delta=delta, borderType=native_border),
            dtype=np.float32,
        )
    except cv2.error as error:
        raise ValueError(f"Convolution failed: {error}") from error
    restored = _restore_channel_axis(filtered, values.shape[2])
    return _crop_filter_result(restored, values.shape, crop)


def _morph_image(
    image: ImageFrame,
    *,
    operation: str,
    kernel_shape: MorphKernelShape,
    kernel_width: int,
    kernel_height: int,
    iterations: int,
    anchor_x: int,
    anchor_y: int,
    border_mode: BorderMode,
    process_alpha: bool,
) -> ImageFrame:
    validate_odd_kernel(kernel_width, kernel_height)
    if isinstance(iterations, bool) or iterations < 1:
        raise ValueError("Morphology iterations must be a positive integer")
    anchor = _validated_anchor(anchor_x, anchor_y, kernel_width, kernel_height)
    effective_x = kernel_width // 2 if anchor[0] == -1 else anchor[0]
    effective_y = kernel_height // 2 if anchor[1] == -1 else anchor[1]
    kernel = cv2.getStructuringElement(
        _CV_MORPH_SHAPE[kernel_shape],
        (kernel_width, kernel_height),
        anchor,
    )
    values, alpha = (
        (image.data, None) if process_alpha else split_alpha(image.data, image.color_space)
    )
    source, native_border, crop = _prepare_native_filter(
        values,
        border_mode,
        left=effective_x * iterations,
        right=(kernel_width - 1 - effective_x) * iterations,
        top=effective_y * iterations,
        bottom=(kernel_height - 1 - effective_y) * iterations,
    )
    native_operation = cv2.dilate if operation == "dilate" else cv2.erode
    try:
        transformed = np.asarray(
            native_operation(
                source,
                kernel,
                anchor=anchor,
                iterations=iterations,
                borderType=native_border,
            ),
            dtype=np.float32,
        )
    except cv2.error as error:
        raise ValueError(f"{operation.capitalize()} failed: {error}") from error
    restored = _restore_channel_axis(transformed, values.shape[2])
    cropped = _crop_filter_result(restored, values.shape, crop)
    result = cropped if process_alpha else recombine_alpha(cropped, alpha, image.color_space)
    return frame_like(image, result)


def _validated_anchor(
    anchor_x: int, anchor_y: int, kernel_width: int, kernel_height: int
) -> tuple[int, int]:
    if (anchor_x, anchor_y) == (-1, -1):
        return (-1, -1)
    if anchor_x < 0 or anchor_y < 0:
        raise ValueError("Morphology anchor must be (-1, -1) or a valid in-kernel position")
    if anchor_x >= kernel_width or anchor_y >= kernel_height:
        raise ValueError("Morphology anchor must lie within the kernel")
    return (anchor_x, anchor_y)


def _prepare_native_filter(
    values: NDArray[np.float32],
    border_mode: BorderMode,
    *,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> tuple[NDArray[np.float32], int, tuple[int, int]]:
    if values.shape[0] == 0 or values.shape[1] == 0:
        raise ValueError("Image width and height must be positive")
    if border_mode is not BorderMode.WRAP:
        return values, cv_border_mode(border_mode), (0, 0)
    source = np.pad(values, ((top, bottom), (left, right), (0, 0)), mode="wrap")
    return np.asarray(source, dtype=np.float32), cv2.BORDER_CONSTANT, (left, top)


def _crop_filter_result(
    values: NDArray[np.float32],
    original_shape: tuple[int, ...],
    crop: tuple[int, int],
) -> NDArray[np.float32]:
    crop_x, crop_y = crop
    return np.asarray(
        values[crop_y : crop_y + original_shape[0], crop_x : crop_x + original_shape[1]],
        dtype=np.float32,
    )


def _gaussian_blur_values(
    values: NDArray[np.float32],
    *,
    kernel_size: tuple[int, int],
    sigma_x: float,
    sigma_y: float,
    border_mode: BorderMode,
    wrap_padding: tuple[int, int],
) -> NDArray[np.float32]:
    if values.shape[0] == 0 or values.shape[1] == 0:
        raise ValueError("Image width and height must be positive")
    source = values
    crop_x = crop_y = 0
    native_border = cv_border_mode(border_mode)
    if border_mode is BorderMode.WRAP:
        crop_x, crop_y = wrap_padding
        source = np.pad(
            values,
            ((crop_y, crop_y), (crop_x, crop_x), (0, 0)),
            mode="wrap",
        )
        # OpenCV filters reject BORDER_WRAP. The wrapped halo makes the bounded crop exact.
        native_border = cv2.BORDER_CONSTANT
    try:
        filtered = np.asarray(
            cv2.GaussianBlur(
                source,
                kernel_size,
                sigmaX=sigma_x,
                sigmaY=sigma_y,
                borderType=native_border,
            ),
            dtype=np.float32,
        )
    except cv2.error as error:
        raise ValueError(f"Gaussian filtering failed: {error}") from error
    restored = _restore_channel_axis(filtered, values.shape[2])
    return np.asarray(
        restored[crop_y : crop_y + values.shape[0], crop_x : crop_x + values.shape[1]],
        dtype=np.float32,
    )


def _restore_channel_axis(data: NDArray[np.float32], channel_count: int) -> NDArray[np.float32]:
    if channel_count == 1 and data.ndim == 2:
        return data[..., None]
    return data


def _noise_seed(seed: int, tick_index: int, *, animate_seed: bool) -> np.random.SeedSequence:
    normalized_seed = seed & ((1 << 64) - 1)
    entropy: int | tuple[int, int] = (
        (normalized_seed, tick_index) if animate_seed else normalized_seed
    )
    return np.random.SeedSequence(entropy)


def _require_non_negative_finite(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")


def _require_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _require_positive_finite(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive")


def _require_normalized_threshold(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")


__all__ = [
    "ConvolutionNormalization",
    "MorphKernelShape",
    "NoiseType",
    "ThresholdMode",
    "add_noise_image",
    "canny_image",
    "convolve_image",
    "dilate_image",
    "erode_image",
    "gaussian_blur_image",
    "high_pass_image",
    "low_pass_image",
    "posterize_image",
    "sharpen_image",
    "threshold_channel",
]
