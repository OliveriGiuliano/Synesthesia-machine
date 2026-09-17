"""Unit tests for the shared presented source-tick builders.

The builders own the runtime-data invariants every source kind must apply:
uint8-to-float32/255 read-only normalization, SRGB frame shaping, and the
FrameContext/index bookkeeping. They are pure, so the invariants are
assertable without any thread, device, or decoded media.
"""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    NoData,
)
from synesthesia_machine.media import (
    PresentedSourceFrame,
    build_outage_frame,
    build_presented_image_frame,
    build_source_context,
    normalize_uint8_rgb,
)

NODE_ID = UUID("00000000-0000-0000-0000-000000000701")


def _rgb() -> np.ndarray:
    data = np.zeros((2, 3, 3), dtype=np.uint8)
    data[0, 0] = (0, 128, 255)
    return data


def test_normalize_uint8_rgb_scales_to_unit_range_and_is_read_only() -> None:
    rgb = normalize_uint8_rgb(_rgb())

    assert rgb.dtype == np.float32
    assert rgb.flags.c_contiguous and not rgb.flags.writeable
    assert rgb[0, 0, 0] == 0.0
    assert rgb[0, 0, 1] == pytest.approx(128.0 / 255.0)
    assert rgb[0, 0, 2] == 1.0
    # The input buffer is untouched: normalization copies before scaling.
    assert _rgb()[0, 0, 2] == 255


@pytest.mark.parametrize(
    "buffer",
    [
        np.zeros((2, 3, 3), dtype=np.float32),
        np.zeros((2, 3), dtype=np.uint8),
        np.zeros((2, 3, 4), dtype=np.uint8),
    ],
)
def test_normalize_uint8_rgb_rejects_non_rgb_buffers(buffer: np.ndarray) -> None:
    with pytest.raises(ValueError, match="HxWx3 uint8 RGB"):
        normalize_uint8_rgb(buffer)


def test_build_presented_image_frame_applies_the_shared_frame_invariants() -> None:
    image = build_presented_image_frame(
        node_id=NODE_ID,
        source_kind="camera",
        rgb_uint8=_rgb(),
        processed_index=7,
        source_frame_index=13,
        source_time_s=-2.0,
        received_monotonic_ns=1000,
        deadline_monotonic_ns=900,
        is_realtime=True,
    )

    assert image.color_space is ColorSpace.SRGB
    assert image.channel_names == ("R", "G", "B")
    assert image.alpha_mode is AlphaMode.NONE
    assert image.provenance.source_kind == "camera"
    assert image.provenance.source_node_id == NODE_ID
    assert image.data.dtype == np.float32
    assert not image.data.flags.writeable
    # Negative source times clamp to 0.0 on the source clock.
    assert image.context.source_time_s == 0.0
    assert image.context.tick_index == 7
    assert image.context.source_frame_index == 13
    assert image.context.received_monotonic_ns == 1000
    assert image.context.deadline_monotonic_ns == 900
    assert image.context.is_realtime is True
    # The frame's context matches the presented tick's context, so the
    # presented source frame accepts it without an explicit context.
    PresentedSourceFrame(image, 7)


def test_build_presented_image_frame_reuses_context_for_every_source_kind() -> None:
    video = build_presented_image_frame(
        node_id=NODE_ID,
        source_kind="video",
        rgb_uint8=_rgb(),
        processed_index=1,
        source_frame_index=1,
        source_time_s=0.5,
        received_monotonic_ns=1,
        deadline_monotonic_ns=50,
    )
    camera = build_presented_image_frame(
        node_id=NODE_ID,
        source_kind="camera",
        rgb_uint8=_rgb(),
        processed_index=1,
        source_frame_index=0,
        source_time_s=0.5,
        received_monotonic_ns=1,
    )

    assert video.provenance.source_kind == "video"
    assert camera.provenance.source_kind == "camera"
    # The invariants are kind-independent: identical shaping, different origin.
    assert video.color_space is camera.color_space is ColorSpace.SRGB
    assert video.channel_names == camera.channel_names
    assert video.alpha_mode is camera.alpha_mode is AlphaMode.NONE


def test_build_outage_frame_carries_an_explicit_no_data_tick() -> None:
    tick = build_outage_frame(
        node_id=NODE_ID,
        processed_index=3,
        source_frame_index=9,
        source_time_s=1.5,
        received_monotonic_ns=77,
        deadline_monotonic_ns=None,
        is_realtime=True,
    )

    assert tick.image is NoData
    assert tick.processed_index == 3
    assert tick.context.tick_index == 3
    assert tick.context.source_frame_index == 9
    assert tick.context.is_realtime is True


def test_build_source_context_is_the_single_owner_of_context_rules() -> None:
    context = build_source_context(
        node_id=NODE_ID,
        tick_index=4,
        source_frame_index=None,
        source_time_s=-0.25,
        received_monotonic_ns=5,
        deadline_monotonic_ns=None,
        is_realtime=False,
    )

    assert context.clock_id == NODE_ID
    assert context.tick_index == 4
    assert context.source_frame_index is None
    assert context.source_time_s == 0.0
    assert context.received_monotonic_ns == 5
    assert context.deadline_monotonic_ns is None
    assert context.is_realtime is False
