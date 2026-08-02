"""Vectorized immutable crop, flip, and rotation algorithms."""

from __future__ import annotations

import math
from enum import StrEnum

import cv2
import numpy as np

from synesthesia_machine.contracts import ColorValue, ImageFrame
from synesthesia_machine.media.colour import color_value_for_space
from synesthesia_machine.media.image_common import (
    BorderMode,
    Interpolation,
    cv_border_mode,
    cv_interpolation,
    frame_like,
)


class CoordinateMode(StrEnum):
    NORMALIZED = "NORMALIZED"
    PIXELS = "PIXELS"


class CropOutOfBounds(StrEnum):
    CLAMP = "CLAMP"
    PAD_CONSTANT = "PAD_CONSTANT"
    ERROR = "ERROR"


class FlipMode(StrEnum):
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"
    BOTH = "BOTH"


def crop_image(
    image: ImageFrame,
    *,
    coordinate_mode: CoordinateMode,
    left: float,
    top: float,
    right: float,
    bottom: float,
    out_of_bounds: CropOutOfBounds,
    pad_colour: ColorValue,
) -> ImageFrame:
    height, width = image.data.shape[:2]
    values = (left, top, right, bottom)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("crop bounds must be finite")
    if coordinate_mode is CoordinateMode.NORMALIZED:
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("normalized crop bounds must be in the range 0..1")
        pixel_left = round(left * width)
        pixel_top = round(top * height)
        pixel_right = round(right * width)
        pixel_bottom = round(bottom * height)
    else:
        pixel_left, pixel_top, pixel_right, pixel_bottom = (
            round(left),
            round(top),
            round(right),
            round(bottom),
        )
    requested_width = pixel_right - pixel_left
    requested_height = pixel_bottom - pixel_top
    if requested_width <= 0 or requested_height <= 0:
        raise ValueError("crop bounds must produce a non-empty image")
    if requested_width > 8192 or requested_height > 8192:
        raise ValueError("crop output dimensions must not exceed 8192")

    outside = pixel_left < 0 or pixel_top < 0 or pixel_right > width or pixel_bottom > height
    if out_of_bounds is CropOutOfBounds.ERROR and outside:
        raise ValueError("crop bounds extend outside the source image")
    if out_of_bounds is CropOutOfBounds.CLAMP:
        clamped_left = min(width, max(0, pixel_left))
        clamped_top = min(height, max(0, pixel_top))
        clamped_right = min(width, max(0, pixel_right))
        clamped_bottom = min(height, max(0, pixel_bottom))
        if clamped_right <= clamped_left or clamped_bottom <= clamped_top:
            raise ValueError("clamped crop is empty")
        return frame_like(
            image,
            image.data[clamped_top:clamped_bottom, clamped_left:clamped_right],
        )
    if not outside:
        return frame_like(image, image.data[pixel_top:pixel_bottom, pixel_left:pixel_right])

    fill = np.asarray(color_value_for_space(pad_colour, image.color_space), dtype=np.float32)
    if fill.shape != (image.data.shape[2],):
        raise ValueError("pad colour does not match the image colour descriptor")
    result = np.empty((requested_height, requested_width, image.data.shape[2]), dtype=np.float32)
    result[...] = fill
    source_left = max(0, pixel_left)
    source_top = max(0, pixel_top)
    source_right = min(width, pixel_right)
    source_bottom = min(height, pixel_bottom)
    if source_right > source_left and source_bottom > source_top:
        destination_left = source_left - pixel_left
        destination_top = source_top - pixel_top
        result[
            destination_top : destination_top + (source_bottom - source_top),
            destination_left : destination_left + (source_right - source_left),
        ] = image.data[source_top:source_bottom, source_left:source_right]
    return frame_like(image, result)


def flip_image(image: ImageFrame, mode: FlipMode) -> ImageFrame:
    code = {
        FlipMode.HORIZONTAL: 1,
        FlipMode.VERTICAL: 0,
        FlipMode.BOTH: -1,
    }[mode]
    result = cv2.flip(image.data, code)
    if result.ndim == 2:
        result = result[..., None]
    return frame_like(image, np.asarray(result, dtype=np.float32))


def rotate_image(
    image: ImageFrame,
    *,
    angle_degrees: float,
    centre_x: float,
    centre_y: float,
    expand_canvas: bool,
    interpolation: Interpolation,
    border_mode: BorderMode,
    border_colour: ColorValue,
) -> ImageFrame:
    if not math.isfinite(angle_degrees):
        raise ValueError("rotation angle must be finite")
    if not 0.0 <= centre_x <= 1.0 or not 0.0 <= centre_y <= 1.0:
        raise ValueError("normalized rotation centre must be in the range 0..1")
    height, width = image.data.shape[:2]
    centre = (centre_x * (width - 1), centre_y * (height - 1))
    matrix = cv2.getRotationMatrix2D(centre, angle_degrees, 1.0)
    target_width, target_height = width, height
    if expand_canvas:
        edge_corners = np.array(
            [
                [-0.5, -0.5, 1.0],
                [width - 0.5, -0.5, 1.0],
                [width - 0.5, height - 0.5, 1.0],
                [-0.5, height - 0.5, 1.0],
            ],
            dtype=np.float64,
        )
        transformed = edge_corners @ matrix.T
        minimum = transformed.min(axis=0)
        maximum = transformed.max(axis=0)
        extent = maximum - minimum
        target_width = math.ceil(round(float(extent[0]), 12))
        target_height = math.ceil(round(float(extent[1]), 12))
        slack_x = target_width - float(extent[0])
        slack_y = target_height - float(extent[1])
        matrix[0, 2] += -0.5 + slack_x / 2.0 - float(minimum[0])
        matrix[1, 2] += -0.5 + slack_y / 2.0 - float(minimum[1])
    if target_width > 8192 or target_height > 8192:
        raise ValueError("rotated output dimensions must not exceed 8192")
    border_value = color_value_for_space(border_colour, image.color_space)
    result = cv2.warpAffine(
        image.data,
        matrix,
        (target_width, target_height),
        flags=cv_interpolation(interpolation),
        borderMode=cv_border_mode(border_mode),
        borderValue=border_value,
    )
    if result.ndim == 2:
        result = result[..., None]
    return frame_like(image, np.asarray(result, dtype=np.float32))


__all__ = [
    "CoordinateMode",
    "CropOutOfBounds",
    "FlipMode",
    "crop_image",
    "flip_image",
    "rotate_image",
]
