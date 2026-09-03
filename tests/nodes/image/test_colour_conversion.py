"""Colour descriptor, conversion, resize, and immutable channel tests."""

from __future__ import annotations

from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    read_only_float32,
)
from synesthesia_machine.media import (
    COLOR_SPACE_DESCRIPTORS,
    FitMode,
    Interpolation,
    convert_image,
    image_to_display_uint8,
    image_to_luminance,
    resize_image,
)
from synesthesia_machine.nodes.image import create_image_definitions

SOURCE_ID = UUID("00000000-0000-0000-0000-000000000301")
NODE_ID = UUID("00000000-0000-0000-0000-000000000302")


def _context() -> FrameContext:
    return FrameContext(SOURCE_ID, 1, 0, 0.0, 1, None, False)


def _image(data: np.ndarray[tuple[int, ...], np.dtype[np.float32]]) -> ImageFrame:
    return ImageFrame(
        read_only_float32(data),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        _context(),
        FrameProvenance(SOURCE_ID, "video"),
    )


def test_registry_covers_all_spaces_with_authoritative_channel_metadata() -> None:
    assert set(COLOR_SPACE_DESCRIPTORS) == set(ColorSpace)
    assert COLOR_SPACE_DESCRIPTORS[ColorSpace.HSV].channels[0].cyclic
    assert COLOR_SPACE_DESCRIPTORS[ColorSpace.LAB].channel_names == ("L*", "a*", "b*")
    assert COLOR_SPACE_DESCRIPTORS[ColorSpace.YCRCB].channels[1].semantic is (
        ChannelSemantic.CHROMA_RED
    )
    assert COLOR_SPACE_DESCRIPTORS[ColorSpace.RGBA].alpha_mode is AlphaMode.STRAIGHT


def test_known_rgb_conversion_values_and_round_trip() -> None:
    image = _image(np.array([[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]], dtype=np.float32))
    hsv = convert_image(image, ColorSpace.HSV)
    assert np.allclose(hsv.data[0, 0], (0.0, 1.0, 1.0), atol=1e-6)
    assert np.allclose(hsv.data[0, 1], (1.0 / 3.0, 1.0, 1.0), atol=1e-6)
    assert hsv.channel_names == ("H", "S", "V")
    restored = convert_image(hsv, ColorSpace.SRGB)
    assert np.allclose(restored.data, image.data, atol=2e-5)


def test_separate_channels_are_read_only_views_with_descriptor_ranges() -> None:
    hsv = convert_image(
        _image(np.array([[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]], dtype=np.float32)),
        ColorSpace.HSV,
    )
    definition = next(
        definition
        for definition in create_image_definitions()
        if definition.type_id == "synmachine.image.separate_channels"
    )
    outputs = definition.runtime_factory(NODE_ID).process({"image": hsv}, {}, hsv.context)
    hue = outputs["channel_1"]
    assert isinstance(hue, ChannelFrame)
    assert hue.semantic is ChannelSemantic.HUE and hue.cyclic
    assert hue.nominal_min == 0.0 and hue.nominal_max == 1.0
    assert np.shares_memory(hue.data, hsv.data)
    assert not hue.data.flags.writeable
    assert set(outputs) == {"channel_1", "channel_2", "channel_3"}


def test_resize_and_luminance_do_not_mutate_shared_input() -> None:
    source_data = np.array([[[0.25, 0.5, 0.75], [1.0, 0.0, 0.0]]], dtype=np.float32)
    image = _image(source_data)
    before = image.data.copy()
    resized = resize_image(
        image,
        4,
        4,
        preserve_aspect=True,
        fit_mode=FitMode.CONTAIN,
        interpolation=Interpolation.AUTO,
    )
    luminance = image_to_luminance(image)

    assert resized.data.shape == (4, 4, 3)
    assert luminance.data.shape == image.data.shape[:2]
    assert not resized.data.flags.writeable and not luminance.data.flags.writeable
    assert np.array_equal(image.data, before)


def test_resize_returns_same_immutable_frame_when_dimensions_already_match() -> None:
    image = _image(np.zeros((4, 4, 3), dtype=np.float32))

    resized = resize_image(
        image,
        4,
        4,
        preserve_aspect=False,
        fit_mode=FitMode.STRETCH,
        interpolation=Interpolation.AUTO,
    )

    assert resized is image


def test_display_transform_sanitizes_non_finite_values() -> None:
    image = _image(np.array([[[np.nan, np.inf, -np.inf], [0.5, 0.25, 1.5]]], dtype=np.float32))
    preview = image_to_display_uint8(image)
    assert preview.dtype == np.uint8 and not preview.flags.writeable
    assert preview[0, 0].tolist() == [0, 255, 0]
    assert preview[0, 1].tolist() == [128, 64, 255]
