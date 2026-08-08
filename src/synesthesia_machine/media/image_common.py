"""Shared immutable image-processing semantics for the built-in node catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelSemantic,
    ColorSpace,
    ImageFrame,
)
from synesthesia_machine.media.colour import color_space_descriptor


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


class BorderMode(StrEnum):
    REFLECT_101 = "REFLECT_101"
    REFLECT = "REFLECT"
    REPLICATE = "REPLICATE"
    CONSTANT = "CONSTANT"
    WRAP = "WRAP"


class ChannelSelection(StrEnum):
    COLOUR = "COLOUR"
    ALL = "ALL"
    CHANNEL_1 = "CHANNEL_1"
    CHANNEL_2 = "CHANNEL_2"
    CHANNEL_3 = "CHANNEL_3"
    CHANNEL_4 = "CHANNEL_4"


_CV_INTERPOLATION = {
    Interpolation.NEAREST: cv2.INTER_NEAREST,
    Interpolation.LINEAR: cv2.INTER_LINEAR,
    Interpolation.AREA: cv2.INTER_AREA,
    Interpolation.CUBIC: cv2.INTER_CUBIC,
    Interpolation.LANCZOS: cv2.INTER_LANCZOS4,
}

_CV_BORDER = {
    BorderMode.REFLECT_101: cv2.BORDER_REFLECT_101,
    BorderMode.REFLECT: cv2.BORDER_REFLECT,
    BorderMode.REPLICATE: cv2.BORDER_REPLICATE,
    BorderMode.CONSTANT: cv2.BORDER_CONSTANT,
    BorderMode.WRAP: cv2.BORDER_WRAP,
}


@dataclass(frozen=True, slots=True)
class FiniteReport:
    nan_count: int
    positive_infinity_count: int
    negative_infinity_count: int

    @property
    def non_finite_count(self) -> int:
        return self.nan_count + self.positive_infinity_count + self.negative_infinity_count


def frame_like(image: ImageFrame, data: NDArray[np.float32]) -> ImageFrame:
    """Create an immutable image with the source descriptor, clock, and provenance."""

    result = np.ascontiguousarray(data, dtype=np.float32)
    if result.ndim == 2:
        result = result[..., None]
    result.flags.writeable = False
    return ImageFrame(
        result,
        image.color_space,
        image.channel_names,
        image.alpha_mode,
        image.context,
        image.provenance,
    )


def alpha_channel_index(color_space: ColorSpace) -> int | None:
    """Return the descriptor-backed alpha index, never a guessed channel position."""

    descriptor = color_space_descriptor(color_space)
    return next(
        (
            index
            for index, channel in enumerate(descriptor.channels)
            if channel.semantic is ChannelSemantic.ALPHA
        ),
        None,
    )


def selected_channel_indices(
    color_space: ColorSpace,
    selection: ChannelSelection,
    *,
    include_alpha: bool = False,
) -> tuple[int, ...]:
    """Resolve a stable channel selector against authoritative descriptor metadata."""

    descriptor = color_space_descriptor(color_space)
    alpha_index = alpha_channel_index(color_space)
    if selection is ChannelSelection.COLOUR:
        indices = tuple(index for index in range(len(descriptor.channels)) if index != alpha_index)
    elif selection is ChannelSelection.ALL:
        indices = tuple(range(len(descriptor.channels)))
    else:
        index = int(selection.value.rsplit("_", 1)[1]) - 1
        if index >= len(descriptor.channels):
            raise ValueError(f"{selection.value} is unavailable for {descriptor.display_name}")
        indices = (index,)
    if include_alpha and alpha_index is not None and alpha_index not in indices:
        indices = (*indices, alpha_index)
    return indices


def split_alpha(
    data: NDArray[np.float32], color_space: ColorSpace
) -> tuple[NDArray[np.float32], NDArray[np.float32] | None]:
    """Return colour channels and descriptor-declared alpha without mutation."""

    alpha_index = alpha_channel_index(color_space)
    if alpha_index is None:
        return data, None
    if alpha_index == 0:
        return data[..., 1:], data[..., 0]
    if alpha_index == data.shape[2] - 1:
        return data[..., :-1], data[..., -1]
    non_alpha = tuple(index for index in range(data.shape[2]) if index != alpha_index)
    return data[..., non_alpha], data[..., alpha_index]


def recombine_alpha(
    channels: NDArray[np.float32],
    alpha: NDArray[np.float32] | None,
    color_space: ColorSpace,
) -> NDArray[np.float32]:
    """Recombine processed channels with preserved alpha in descriptor order."""

    alpha_index = alpha_channel_index(color_space)
    if alpha_index is None:
        if alpha is not None:
            raise ValueError("cannot attach alpha to a colour space without an alpha channel")
        return np.asarray(channels, dtype=np.float32)
    if alpha is None:
        raise ValueError("alpha data is required by the target colour descriptor")
    result = np.empty((*channels.shape[:2], channels.shape[2] + 1), dtype=np.float32)
    non_alpha = tuple(index for index in range(result.shape[2]) if index != alpha_index)
    result[..., non_alpha] = channels
    result[..., alpha_index] = alpha
    return result


def validate_odd_kernel(width: int, height: int, *, maximum: int | None = None) -> None:
    """Reject malformed native filter dimensions at the runtime boundary."""

    if width <= 0 or height <= 0 or width % 2 == 0 or height % 2 == 0:
        raise ValueError("kernel width and height must be positive odd integers")
    if maximum is not None and (width > maximum or height > maximum):
        raise ValueError(f"kernel width and height must not exceed {maximum}")


def finite_report(data: NDArray[np.float32]) -> FiniteReport:
    return FiniteReport(
        int(np.count_nonzero(np.isnan(data))),
        int(np.count_nonzero(np.isposinf(data))),
        int(np.count_nonzero(np.isneginf(data))),
    )


def sanitize_finite(
    data: NDArray[np.float32],
    *,
    nan: float = 0.0,
    positive_infinity: float = 1.0,
    negative_infinity: float = 0.0,
) -> tuple[NDArray[np.float32], FiniteReport]:
    """Return a finite float32 copy and an exact sanitation report."""

    report = finite_report(data)
    result = np.nan_to_num(
        data,
        nan=nan,
        posinf=positive_infinity,
        neginf=negative_infinity,
    ).astype(np.float32, copy=False)
    return np.asarray(result, dtype=np.float32), report


def cv_border_mode(mode: BorderMode) -> int:
    return _CV_BORDER[mode]


def cv_interpolation(
    interpolation: Interpolation,
    *,
    source_size: tuple[int, int] | None = None,
    target_size: tuple[int, int] | None = None,
) -> int:
    if interpolation is not Interpolation.AUTO:
        return _CV_INTERPOLATION[interpolation]
    if source_size is not None and target_size is not None:
        source_width, source_height = source_size
        target_width, target_height = target_size
        if target_width < source_width or target_height < source_height:
            return cv2.INTER_AREA
    return cv2.INTER_LINEAR


__all__ = [
    "BorderMode",
    "ChannelSelection",
    "FiniteReport",
    "FitMode",
    "Interpolation",
    "alpha_channel_index",
    "cv_border_mode",
    "cv_interpolation",
    "finite_report",
    "frame_like",
    "recombine_alpha",
    "sanitize_finite",
    "selected_channel_indices",
    "split_alpha",
    "validate_odd_kernel",
]
