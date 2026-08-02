"""Deterministic immutable image resizing helpers."""

from __future__ import annotations

import cv2
import numpy as np

from synesthesia_machine.contracts.runtime_values import ImageFrame
from synesthesia_machine.media.image_common import (
    FitMode,
    Interpolation,
    cv_interpolation,
    frame_like,
)


def resize_image(
    image: ImageFrame,
    width: int,
    height: int,
    *,
    preserve_aspect: bool,
    fit_mode: FitMode,
    interpolation: Interpolation,
) -> ImageFrame:
    if not 1 <= width <= 8192 or not 1 <= height <= 8192:
        raise ValueError("resize width and height must be in the range 1..8192")
    source_height, source_width = image.data.shape[:2]
    if not preserve_aspect or fit_mode is FitMode.STRETCH:
        result = _resize(image.data, width, height, interpolation)
    elif fit_mode is FitMode.COVER:
        cropped = _central_cover_crop(image.data, width, height)
        result = _resize(cropped, width, height, interpolation)
    else:
        width_scale = width / source_width
        height_scale = height / source_height
        if width_scale <= height_scale:
            scaled_width = width
            scaled_height = min(height, max(1, round(source_height * width_scale)))
        else:
            scaled_width = min(width, max(1, round(source_width * height_scale)))
            scaled_height = height
        scaled = _resize(image.data, scaled_width, scaled_height, interpolation)
        result = np.zeros((height, width, image.data.shape[2]), dtype=np.float32)
        top = (height - scaled_height) // 2
        left = (width - scaled_width) // 2
        result[top : top + scaled_height, left : left + scaled_width] = scaled
    return frame_like(image, result)


def _central_cover_crop(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    target_width: int,
    target_height: int,
) -> np.ndarray[tuple[int, ...], np.dtype[np.float32]]:
    """Crop centrally to the target aspect before one bounded resize operation."""

    source_height, source_width = data.shape[:2]
    source_aspect = source_width / source_height
    target_aspect = target_width / target_height
    if source_aspect > target_aspect:
        crop_width = min(source_width, max(1, round(source_height * target_aspect)))
        left = (source_width - crop_width) // 2
        return data[:, left : left + crop_width]
    crop_height = min(source_height, max(1, round(source_width / target_aspect)))
    top = (source_height - crop_height) // 2
    return data[top : top + crop_height, :]


def _resize(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    width: int,
    height: int,
    interpolation: Interpolation,
) -> np.ndarray[tuple[int, ...], np.dtype[np.float32]]:
    source_height, source_width = data.shape[:2]
    algorithm = cv_interpolation(
        interpolation,
        source_size=(source_width, source_height),
        target_size=(width, height),
    )
    resized = cv2.resize(data, (width, height), interpolation=algorithm)
    if resized.ndim == 2:
        resized = resized[..., None]
    return np.asarray(resized, dtype=np.float32)
