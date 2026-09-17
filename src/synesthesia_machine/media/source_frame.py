"""Presented source-tick invariants shared by every source kind.

A presented tick is the unit a source hands to the engine source mailbox: one
read-only normalized image (or an explicit ``NoData`` outage) plus the
``FrameContext`` that pins the source-clock identity. Every source kind builds
its ticks through the builders here, so the runtime data invariants — uint8
normalization, SRGB frame shaping, and the context/index bookkeeping — have
one owner, and a new source kind inherits them without copying the rules.
The module is pure: no threads, devices, or state, so the builders are
directly unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    NoData,
    NoDataType,
)


@dataclass(frozen=True, slots=True, init=False)
class PresentedSourceFrame:
    """One image or explicit outage tick ready for the engine source mailbox."""

    image: ImageFrame | NoDataType
    processed_index: int
    context: FrameContext

    def __init__(
        self,
        image: ImageFrame | NoDataType,
        processed_index: int,
        context: FrameContext | None = None,
    ) -> None:
        resolved_context = image.context if isinstance(image, ImageFrame) else context
        if resolved_context is None:
            raise ValueError("NoData source frames require an explicit frame context")
        object.__setattr__(self, "image", image)
        object.__setattr__(self, "processed_index", processed_index)
        object.__setattr__(self, "context", resolved_context)
        if self.processed_index < 1:
            raise ValueError("processed_index must start at 1")
        if self.context.tick_index != self.processed_index:
            raise ValueError("source tick and processed index must match")
        if isinstance(self.image, ImageFrame) and self.image.context != self.context:
            raise ValueError("image and source-frame contexts must match")


def normalize_uint8_rgb(rgb_uint8: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Normalize an HxWx3 RGB uint8 buffer to read-only float32 in ``[0, 1]``.

    The result is C-contiguous and non-writable: one presented frame may feed
    many consumers, so every consumer treats the array as immutable.
    """

    if rgb_uint8.dtype != np.uint8 or rgb_uint8.ndim != 3 or rgb_uint8.shape[2] != 3:
        raise ValueError(f"expected HxWx3 uint8 RGB, got {rgb_uint8.shape} {rgb_uint8.dtype}")
    rgb = np.ascontiguousarray(rgb_uint8, dtype=np.float32)
    rgb *= np.float32(1.0 / 255.0)
    rgb.flags.writeable = False
    return rgb


def build_source_context(
    *,
    node_id: UUID,
    tick_index: int,
    source_frame_index: int | None,
    source_time_s: float,
    received_monotonic_ns: int,
    deadline_monotonic_ns: int | None,
    is_realtime: bool,
) -> FrameContext:
    """Build the ``FrameContext`` of one presented tick.

    ``tick_index`` must equal the tick's processed index (the ``presented
    source frame`` coupling enforces it); negative source times clamp to 0.0
    so a source clock never carries a negative time.
    """

    return FrameContext(
        clock_id=node_id,
        tick_index=tick_index,
        source_frame_index=source_frame_index,
        source_time_s=max(0.0, source_time_s),
        received_monotonic_ns=received_monotonic_ns,
        deadline_monotonic_ns=deadline_monotonic_ns,
        is_realtime=is_realtime,
    )


def build_presented_image_frame(
    *,
    node_id: UUID,
    source_kind: str,
    rgb_uint8: NDArray[np.uint8],
    processed_index: int,
    source_frame_index: int | None,
    source_time_s: float,
    received_monotonic_ns: int,
    deadline_monotonic_ns: int | None = None,
    is_realtime: bool = True,
) -> ImageFrame:
    """Shape one presented image tick: normalize, stamp, and provenance it.

    The frame is SRGB (R, G, B) with no alpha, carries the source context for
    the given tick, and records ``source_kind`` (e.g. ``"video"``,
    ``"camera"``) in its provenance.
    """

    context = build_source_context(
        node_id=node_id,
        tick_index=processed_index,
        source_frame_index=source_frame_index,
        source_time_s=source_time_s,
        received_monotonic_ns=received_monotonic_ns,
        deadline_monotonic_ns=deadline_monotonic_ns,
        is_realtime=is_realtime,
    )
    return ImageFrame(
        normalize_uint8_rgb(rgb_uint8),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(node_id, source_kind),
    )


def build_outage_frame(
    *,
    node_id: UUID,
    processed_index: int,
    source_frame_index: int | None,
    source_time_s: float,
    received_monotonic_ns: int,
    deadline_monotonic_ns: int | None = None,
    is_realtime: bool = True,
) -> PresentedSourceFrame:
    """Build an explicit ``NoData`` outage tick under the same context rules."""

    context = build_source_context(
        node_id=node_id,
        tick_index=processed_index,
        source_frame_index=source_frame_index,
        source_time_s=source_time_s,
        received_monotonic_ns=received_monotonic_ns,
        deadline_monotonic_ns=deadline_monotonic_ns,
        is_realtime=is_realtime,
    )
    return PresentedSourceFrame(NoData, processed_index, context)


__all__ = [
    "PresentedSourceFrame",
    "build_outage_frame",
    "build_presented_image_frame",
    "build_source_context",
    "normalize_uint8_rgb",
]
