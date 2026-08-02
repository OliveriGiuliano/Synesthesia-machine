"""Vectorized immutable image-filter algorithms."""

from __future__ import annotations

import math
from enum import StrEnum

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import ImageFrame
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


__all__ = [
    "NoiseType",
    "add_noise_image",
    "gaussian_blur_image",
    "posterize_image",
    "sharpen_image",
]
