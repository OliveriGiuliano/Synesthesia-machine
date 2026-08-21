"""Deterministic immutable image fixtures for image-node conformance tests."""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    read_only_float32,
)

SOURCE_ID = UUID("00000000-0000-0000-0000-000000005010")


@pytest.fixture
def image_context() -> FrameContext:
    return FrameContext(SOURCE_ID, 7, 6, 0.2, 100, 200, False)


@pytest.fixture
def rgb_image(image_context: FrameContext) -> ImageFrame:
    data = np.arange(4 * 6 * 3, dtype=np.float32).reshape(4, 6, 3) / np.float32(71.0)
    return ImageFrame(
        read_only_float32(data),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        image_context,
        FrameProvenance(SOURCE_ID, "image-node-test"),
    )


@pytest.fixture
def rgba_image(image_context: FrameContext) -> ImageFrame:
    data = np.zeros((2, 3, 4), dtype=np.float32)
    data[..., :3] = np.arange(18, dtype=np.float32).reshape(2, 3, 3) / np.float32(17.0)
    data[..., 3] = np.array([[0.0, 0.5, 1.0], [1.0, 0.5, 0.0]], dtype=np.float32)
    return ImageFrame(
        read_only_float32(data),
        ColorSpace.RGBA,
        ("R", "G", "B", "A"),
        AlphaMode.STRAIGHT,
        image_context,
        FrameProvenance(SOURCE_ID, "image-node-test"),
    )
