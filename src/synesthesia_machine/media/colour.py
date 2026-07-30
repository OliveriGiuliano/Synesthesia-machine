"""Authoritative colour-space metadata, conversions, and display transforms."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts.runtime_values import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    ImageFrame,
    read_only_float32,
)


@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    name: str
    semantic: ChannelSemantic
    nominal_min: float
    nominal_max: float
    cyclic: bool = False


@dataclass(frozen=True, slots=True)
class ColorSpaceDescriptor:
    color_space: ColorSpace
    display_name: str
    channels: tuple[ChannelDescriptor, ...]
    alpha_mode: AlphaMode = AlphaMode.NONE

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(channel.name for channel in self.channels)


_RGB_CHANNELS = (
    ChannelDescriptor("R", ChannelSemantic.RED, 0.0, 1.0),
    ChannelDescriptor("G", ChannelSemantic.GREEN, 0.0, 1.0),
    ChannelDescriptor("B", ChannelSemantic.BLUE, 0.0, 1.0),
)

COLOR_SPACE_DESCRIPTORS = MappingProxyType(
    {
        ColorSpace.LINEAR_RGB: ColorSpaceDescriptor(
            ColorSpace.LINEAR_RGB, "Linear RGB", _RGB_CHANNELS
        ),
        ColorSpace.SRGB: ColorSpaceDescriptor(ColorSpace.SRGB, "RGB (sRGB)", _RGB_CHANNELS),
        ColorSpace.RGBA: ColorSpaceDescriptor(
            ColorSpace.RGBA,
            "RGBA (sRGB)",
            (
                *_RGB_CHANNELS,
                ChannelDescriptor("A", ChannelSemantic.ALPHA, 0.0, 1.0),
            ),
            AlphaMode.STRAIGHT,
        ),
        ColorSpace.HSV: ColorSpaceDescriptor(
            ColorSpace.HSV,
            "HSV",
            (
                ChannelDescriptor("H", ChannelSemantic.HUE, 0.0, 1.0, True),
                ChannelDescriptor("S", ChannelSemantic.SATURATION, 0.0, 1.0),
                ChannelDescriptor("V", ChannelSemantic.VALUE, 0.0, 1.0),
            ),
        ),
        ColorSpace.HSL: ColorSpaceDescriptor(
            ColorSpace.HSL,
            "HSL",
            (
                ChannelDescriptor("H", ChannelSemantic.HUE, 0.0, 1.0, True),
                ChannelDescriptor("S", ChannelSemantic.SATURATION, 0.0, 1.0),
                ChannelDescriptor("L", ChannelSemantic.LIGHTNESS, 0.0, 1.0),
            ),
        ),
        ColorSpace.LAB: ColorSpaceDescriptor(
            ColorSpace.LAB,
            "CIE Lab",
            (
                ChannelDescriptor("L*", ChannelSemantic.LIGHTNESS, 0.0, 100.0),
                ChannelDescriptor("a*", ChannelSemantic.LAB_A, -127.0, 127.0),
                ChannelDescriptor("b*", ChannelSemantic.LAB_B, -127.0, 127.0),
            ),
        ),
        ColorSpace.YCRCB: ColorSpaceDescriptor(
            ColorSpace.YCRCB,
            "YCrCb",
            (
                ChannelDescriptor("Y", ChannelSemantic.LUMINANCE, 0.0, 1.0),
                ChannelDescriptor("Cr", ChannelSemantic.CHROMA_RED, 0.0, 1.0),
                ChannelDescriptor("Cb", ChannelSemantic.CHROMA_BLUE, 0.0, 1.0),
            ),
        ),
    }
)


def color_space_descriptor(color_space: ColorSpace) -> ColorSpaceDescriptor:
    return COLOR_SPACE_DESCRIPTORS[color_space]


def convert_image(image: ImageFrame, target: ColorSpace) -> ImageFrame:
    """Convert explicitly between registered spaces without mutating input storage."""

    if image.color_space is target:
        return image
    rgb, alpha = _to_srgb(image.data, image.color_space)
    converted = _from_srgb(rgb, alpha, target)
    descriptor = color_space_descriptor(target)
    return ImageFrame(
        read_only_float32(converted),
        target,
        descriptor.channel_names,
        descriptor.alpha_mode,
        image.context,
        image.provenance,
    )


def image_to_luminance(image: ImageFrame) -> ChannelFrame:
    """Return linear-light CIE-style relative luminance as an immutable channel."""

    rgb, _ = _to_srgb(image.data, image.color_space)
    linear = _srgb_to_linear(rgb)
    luminance = (
        linear[..., 0] * np.float32(0.2126)
        + linear[..., 1] * np.float32(0.7152)
        + linear[..., 2] * np.float32(0.0722)
    )
    data = read_only_float32(np.asarray(luminance, dtype=np.float32))
    return ChannelFrame(data, ChannelSemantic.LUMINANCE, 0.0, 1.0, False, image.context)


def image_to_display_uint8(image: ImageFrame) -> NDArray[np.uint8]:
    """Sanitize and transform an image into immutable RGB/RGBA preview bytes."""

    safe = np.nan_to_num(image.data, nan=0.0, posinf=1.0, neginf=0.0).astype(np.float32, copy=False)
    rgb, alpha = _to_srgb(safe, image.color_space)
    rgb_bytes = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    if alpha is None:
        result = rgb_bytes
    else:
        alpha_bytes = np.rint(np.clip(alpha, 0.0, 1.0) * 255.0).astype(np.uint8)
        result = np.concatenate((rgb_bytes, alpha_bytes[..., None]), axis=2)
    immutable = np.ascontiguousarray(result)
    immutable.flags.writeable = False
    return immutable


def _to_srgb(
    data: NDArray[np.float32], color_space: ColorSpace
) -> tuple[NDArray[np.float32], NDArray[np.float32] | None]:
    alpha: NDArray[np.float32] | None = None
    if color_space is ColorSpace.SRGB:
        rgb = data
    elif color_space is ColorSpace.RGBA:
        rgb = data[..., :3]
        alpha = data[..., 3]
    elif color_space is ColorSpace.LINEAR_RGB:
        rgb = _linear_to_srgb(data)
    elif color_space is ColorSpace.HSV:
        cv_data = np.array(data, dtype=np.float32, order="C", copy=True)
        cv_data[..., 0] *= np.float32(360.0)
        rgb = cv2.cvtColor(cv_data, cv2.COLOR_HSV2RGB)
    elif color_space is ColorSpace.HSL:
        cv_data = np.empty_like(data)
        cv_data[..., 0] = data[..., 0] * np.float32(360.0)
        cv_data[..., 1] = data[..., 2]
        cv_data[..., 2] = data[..., 1]
        rgb = cv2.cvtColor(cv_data, cv2.COLOR_HLS2RGB)
    elif color_space is ColorSpace.LAB:
        rgb = cv2.cvtColor(data, cv2.COLOR_Lab2RGB)
    elif color_space is ColorSpace.YCRCB:
        rgb = cv2.cvtColor(data, cv2.COLOR_YCrCb2RGB)
    else:  # pragma: no cover - exhaustive StrEnum guard
        raise ValueError(f"Unsupported colour space: {color_space}")
    return np.asarray(rgb, dtype=np.float32), alpha


def _from_srgb(
    rgb: NDArray[np.float32], alpha: NDArray[np.float32] | None, target: ColorSpace
) -> NDArray[np.float32]:
    if target is ColorSpace.SRGB:
        return rgb
    if target is ColorSpace.RGBA:
        target_alpha = np.ones(rgb.shape[:2], dtype=np.float32) if alpha is None else alpha
        return np.concatenate((rgb, target_alpha[..., None]), axis=2)
    if target is ColorSpace.LINEAR_RGB:
        return _srgb_to_linear(rgb)
    if target is ColorSpace.HSV:
        result = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        result[..., 0] /= np.float32(360.0)
        return np.asarray(result, dtype=np.float32)
    if target is ColorSpace.HSL:
        hls = cv2.cvtColor(rgb, cv2.COLOR_RGB2HLS)
        result = np.empty_like(hls)
        result[..., 0] = hls[..., 0] / np.float32(360.0)
        result[..., 1] = hls[..., 2]
        result[..., 2] = hls[..., 1]
        return np.asarray(result, dtype=np.float32)
    if target is ColorSpace.LAB:
        return np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2Lab), dtype=np.float32)
    if target is ColorSpace.YCRCB:
        return np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb), dtype=np.float32)
    raise ValueError(f"Unsupported colour space: {target}")  # pragma: no cover


def _srgb_to_linear(data: NDArray[np.float32]) -> NDArray[np.float32]:
    return np.where(
        data <= np.float32(0.04045),
        data / np.float32(12.92),
        ((data + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4),
    ).astype(np.float32, copy=False)


def _linear_to_srgb(data: NDArray[np.float32]) -> NDArray[np.float32]:
    positive = np.maximum(data, np.float32(0.0))
    return np.where(
        positive <= np.float32(0.0031308),
        positive * np.float32(12.92),
        np.float32(1.055) * positive ** np.float32(1.0 / 2.4) - np.float32(0.055),
    ).astype(np.float32, copy=False)
