"""Qt-free multi-input image compositing and channel assembly algorithms."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    ColorSpace,
    FrameProvenance,
    ImageFrame,
    read_only_float32,
)
from synesthesia_machine.media.colour import color_space_descriptor
from synesthesia_machine.media.image_common import frame_like, recombine_alpha, split_alpha


class BlendMode(StrEnum):
    NORMAL = "NORMAL"
    ADD = "ADD"
    MULTIPLY = "MULTIPLY"
    SCREEN = "SCREEN"
    DIFFERENCE = "DIFFERENCE"
    LIGHTEN = "LIGHTEN"
    DARKEN = "DARKEN"


class AlphaPolicy(StrEnum):
    COMPOSITE = "COMPOSITE"
    PRESERVE_A = "PRESERVE_A"
    PRESERVE_B = "PRESERVE_B"


def blend_images(
    a: ImageFrame,
    b: ImageFrame,
    *,
    blend_mode: BlendMode,
    opacity: float,
    alpha_policy: AlphaPolicy,
    mask: ChannelFrame | None = None,
) -> ImageFrame:
    """Blend image ``b`` over image ``a`` without implicit conversion or resizing."""

    _validate_blend_inputs(a, b, mask)
    if not math.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
        raise ValueError("blend opacity must be finite and between 0 and 1")

    amount: NDArray[np.float32]
    if mask is None:
        amount = np.full(a.data.shape[:2], opacity, dtype=np.float32)
    else:
        if not np.isfinite(mask.data).all():
            raise ValueError("blend mask values must be finite")
        amount = np.asarray(
            mask.data * np.float32(opacity),
            dtype=np.float32,
        )

    a_colour, a_alpha = split_alpha(a.data, a.color_space)
    b_colour, b_alpha = split_alpha(b.data, b.color_space)
    blended = _blend_values(a_colour, b_colour, blend_mode)
    amount_3d = amount[..., None]

    if alpha_policy is AlphaPolicy.COMPOSITE:
        a_coverage = np.ones(a.data.shape[:2], dtype=np.float32) if a_alpha is None else a_alpha
        b_coverage = np.ones(b.data.shape[:2], dtype=np.float32) if b_alpha is None else b_alpha
        source_coverage = b_coverage * amount
        output_alpha = source_coverage + a_coverage * (np.float32(1.0) - source_coverage)
        premultiplied = (
            (np.float32(1.0) - source_coverage[..., None]) * a_coverage[..., None] * a_colour
            + (np.float32(1.0) - a_coverage[..., None]) * source_coverage[..., None] * b_colour
            + a_coverage[..., None] * source_coverage[..., None] * blended
        )
        colour = np.zeros_like(premultiplied)
        np.divide(
            premultiplied,
            output_alpha[..., None],
            out=colour,
            where=output_alpha[..., None] != 0.0,
        )
        output_alpha_or_none = (
            np.asarray(output_alpha, dtype=np.float32) if a_alpha is not None else None
        )
    else:
        colour = a_colour + (blended - a_colour) * amount_3d
        output_alpha_or_none = a_alpha if alpha_policy is AlphaPolicy.PRESERVE_A else b_alpha

    return frame_like(
        a,
        recombine_alpha(
            np.asarray(colour, dtype=np.float32),
            output_alpha_or_none,
            a.color_space,
        ),
    )


def combine_channels(
    channels: Sequence[ChannelFrame],
    target_colour_space: ColorSpace,
    provenance: FrameProvenance,
) -> ImageFrame:
    """Assemble descriptor-ordered channels into one immutable image frame."""

    descriptor = color_space_descriptor(target_colour_space)
    if len(channels) != len(descriptor.channels):
        raise ValueError(
            f"{descriptor.display_name} requires exactly {len(descriptor.channels)} channels"
        )
    first = channels[0]
    for index, (channel, expected) in enumerate(
        zip(channels, descriptor.channels, strict=True),
        start=1,
    ):
        if channel.semantic is not expected.semantic:
            raise ValueError(
                f"channel_{index} must have {expected.semantic.value} semantics, "
                f"got {channel.semantic.value}"
            )
        if channel.data.shape != first.data.shape:
            raise ValueError("combined channels must have equal dimensions")
        if channel.context.clock_id != first.context.clock_id:
            raise ValueError("combined channels must share one source clock")

    data = np.stack(tuple(channel.data for channel in channels), axis=2)
    return ImageFrame(
        read_only_float32(data),
        target_colour_space,
        descriptor.channel_names,
        descriptor.alpha_mode,
        first.context,
        provenance,
    )


def _validate_blend_inputs(
    a: ImageFrame,
    b: ImageFrame,
    mask: ChannelFrame | None,
) -> None:
    if a.data.shape != b.data.shape:
        raise ValueError("blended images must have equal dimensions and channel counts")
    if a.context.clock_id != b.context.clock_id:
        raise ValueError("blended images must share one source clock")
    if (
        a.color_space is not b.color_space
        or a.channel_names != b.channel_names
        or a.alpha_mode is not b.alpha_mode
    ):
        raise ValueError("blended images must have equal colour descriptors")
    if mask is not None:
        if mask.data.shape != a.data.shape[:2]:
            raise ValueError("blend mask dimensions must match the images")
        if mask.context.clock_id != a.context.clock_id:
            raise ValueError("blend mask must share the image source clock")


def _blend_values(
    a: NDArray[np.float32],
    b: NDArray[np.float32],
    mode: BlendMode,
) -> NDArray[np.float32]:
    if mode is BlendMode.NORMAL:
        return b
    if mode is BlendMode.ADD:
        return a + b
    if mode is BlendMode.MULTIPLY:
        return a * b
    if mode is BlendMode.SCREEN:
        return np.asarray(
            np.float32(1.0) - (np.float32(1.0) - a) * (np.float32(1.0) - b),
            dtype=np.float32,
        )
    if mode is BlendMode.DIFFERENCE:
        return np.abs(a - b)
    if mode is BlendMode.LIGHTEN:
        return np.maximum(a, b)
    if mode is BlendMode.DARKEN:
        return np.minimum(a, b)
    raise ValueError(f"unsupported blend mode: {mode}")  # pragma: no cover


__all__ = [
    "AlphaPolicy",
    "BlendMode",
    "blend_images",
    "combine_channels",
]
