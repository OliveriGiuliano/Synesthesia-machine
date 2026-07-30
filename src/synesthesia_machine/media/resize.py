"""Deterministic immutable image resizing helpers."""

from __future__ import annotations

from enum import StrEnum

import cv2
import numpy as np

from synesthesia_machine.contracts.runtime_values import ImageFrame, read_only_float32


class FitMode(StrEnum):
    STRETCH = "STRETCH"
    CONTAIN = "CONTAIN"
    COVER = "COVER"


class Interpolation(StrEnum):
    AUTO = "AUTO"
    NEAREST = "NEAREST"
    LINEAR = "LINEAR"
    AREA = "AREA"
    CUBIC = "CUBIC"
    LANCZOS = "LANCZOS"


_CV_INTERPOLATION = {
    Interpolation.NEAREST: cv2.INTER_NEAREST,
    Interpolation.LINEAR: cv2.INTER_LINEAR,
    Interpolation.AREA: cv2.INTER_AREA,
    Interpolation.CUBIC: cv2.INTER_CUBIC,
    Interpolation.LANCZOS: cv2.INTER_LANCZOS4,
}


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
    else:
        width_scale = width / source_width
        height_scale = height / source_height
        scale = (
            min(width_scale, height_scale)
            if fit_mode is FitMode.CONTAIN
            else max(width_scale, height_scale)
        )
        scaled_width = max(1, round(source_width * scale))
        scaled_height = max(1, round(source_height * scale))
        scaled = _resize(image.data, scaled_width, scaled_height, interpolation)
        if fit_mode is FitMode.CONTAIN:
            result = np.zeros((height, width, image.data.shape[2]), dtype=np.float32)
            top = (height - scaled_height) // 2
            left = (width - scaled_width) // 2
            result[top : top + scaled_height, left : left + scaled_width] = scaled
        else:
            top = (scaled_height - height) // 2
            left = (scaled_width - width) // 2
            result = scaled[top : top + height, left : left + width]
    return ImageFrame(
        read_only_float32(result),
        image.color_space,
        image.channel_names,
        image.alpha_mode,
        image.context,
        image.provenance,
    )


def _resize(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    width: int,
    height: int,
    interpolation: Interpolation,
) -> np.ndarray[tuple[int, ...], np.dtype[np.float32]]:
    if interpolation is Interpolation.AUTO:
        source_height, source_width = data.shape[:2]
        algorithm = (
            cv2.INTER_AREA if width < source_width or height < source_height else cv2.INTER_LINEAR
        )
    else:
        algorithm = _CV_INTERPOLATION[interpolation]
    resized = cv2.resize(data, (width, height), interpolation=algorithm)
    if resized.ndim == 2:
        resized = resized[..., None]
    return np.asarray(resized, dtype=np.float32)
