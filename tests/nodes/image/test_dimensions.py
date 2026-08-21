"""Image dimension algorithm and conformance tests."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest
from tests.support.image_conformance import (
    assert_image_conformance,
    assert_scheduler_propagates_no_data,
)

from synesthesia_machine.contracts import ColorValue, ImageFrame, ParameterValue, RuntimeValue
from synesthesia_machine.nodes import ExpectedNodeError
from synesthesia_machine.nodes.image import create_image_definitions

NODE_ID = UUID("00000000-0000-0000-0000-000000005020")
BATCH1_IDS = (
    "synmachine.image.resize",
    "synmachine.image.crop",
    "synmachine.image.flip",
    "synmachine.image.rotate",
)


def _definition(type_id: str):
    return next(item for item in create_image_definitions() if item.type_id == type_id)


def _process(
    type_id: str,
    image: ImageFrame,
    overrides: Mapping[str, ParameterValue] | None = None,
    connected: Mapping[str, RuntimeValue] | None = None,
) -> ImageFrame:
    definition = _definition(type_id)
    parameters, errors = definition.parameter_values(overrides or {})
    assert not errors
    inputs: dict[str, RuntimeValue] = {"image": image}
    inputs.update(connected or {})
    output = definition.runtime_factory(NODE_ID).process(inputs, parameters, image.context)["image"]
    assert isinstance(output, ImageFrame)
    return output


@pytest.mark.parametrize("type_id", BATCH1_IDS)
def test_batch1_definitions_propagate_no_data(type_id: str) -> None:
    assert_scheduler_propagates_no_data(_definition(type_id), {})


def test_resize_contain_uses_transparent_alpha_padding_and_connected_dimensions(
    rgba_image: ImageFrame,
) -> None:
    before = rgba_image.data.copy()
    result = _process(
        "synmachine.image.resize",
        rgba_image,
        {"preserve_aspect": True, "fit_mode": "CONTAIN", "interpolation": "NEAREST"},
        {"width": 6, "height": 6},
    )
    assert_image_conformance(rgba_image, result, before, expected_shape=(6, 6, 4))
    assert np.all(result.data[0, :, :] == 0.0)
    assert np.all(result.data[-1, :, :] == 0.0)


def test_resize_cover_crops_centrally_before_bounded_resize(rgb_image: ImageFrame) -> None:
    before = rgb_image.data.copy()
    result = _process(
        "synmachine.image.resize",
        rgb_image,
        {
            "width": 2,
            "height": 2,
            "preserve_aspect": True,
            "fit_mode": "COVER",
            "interpolation": "NEAREST",
        },
    )
    assert_image_conformance(rgb_image, result, before, expected_shape=(2, 2, 3))
    assert np.array_equal(result.data, rgb_image.data[::2, 1:5:2])


def test_crop_normalized_and_pixel_padding_have_exclusive_bounds(rgb_image: ImageFrame) -> None:
    before = rgb_image.data.copy()
    normalized = _process(
        "synmachine.image.crop",
        rgb_image,
        {"left": 0.0, "top": 0.25, "right": 0.5, "bottom": 0.75},
    )
    assert_image_conformance(rgb_image, normalized, before, expected_shape=(2, 3, 3))
    assert np.array_equal(normalized.data, rgb_image.data[1:3, 0:3])

    padded = _process(
        "synmachine.image.crop",
        rgb_image,
        {
            "coordinate_mode": "PIXELS",
            "left": -1.0,
            "top": -1.0,
            "right": 2.0,
            "bottom": 2.0,
            "out_of_bounds": "PAD_CONSTANT",
            "pad_colour": ColorValue(0.0, 0.0, 0.0, 1.0),
        },
    )
    assert padded.data.shape == (3, 3, 3)
    assert np.all(padded.data[0] == 0.0)
    assert np.all(padded.data[:, 0] == 0.0)
    assert np.array_equal(padded.data[1:, 1:], rgb_image.data[:2, :2])


def test_crop_rejects_empty_and_error_policy_out_of_bounds(rgb_image: ImageFrame) -> None:
    definition = _definition("synmachine.image.crop")
    parameters, errors = definition.parameter_values(
        {"coordinate_mode": "PIXELS", "left": -1.0, "right": 2.0, "out_of_bounds": "ERROR"}
    )
    assert not errors
    with pytest.raises(ExpectedNodeError, match="outside"):
        definition.runtime_factory(NODE_ID).process(
            {"image": rgb_image}, parameters, rgb_image.context
        )
    _, empty_errors = definition.parameter_values({"left": 0.5, "right": 0.5})
    assert "right must be greater than left" in empty_errors


@pytest.mark.parametrize("mode", ["HORIZONTAL", "VERTICAL", "BOTH"])
def test_flip_modes_process_every_channel_without_mutation(
    rgba_image: ImageFrame, mode: str
) -> None:
    before = rgba_image.data.copy()
    result = _process("synmachine.image.flip", rgba_image, {"mode": mode})
    assert_image_conformance(rgba_image, result, before, expected_shape=rgba_image.data.shape)
    expected = {
        "HORIZONTAL": before[:, ::-1],
        "VERTICAL": before[::-1, :],
        "BOTH": before[::-1, ::-1],
    }[mode]
    assert np.array_equal(result.data, expected)


def test_rotate_positive_angle_expands_canvas_and_preserves_alpha(
    rgba_image: ImageFrame,
) -> None:
    before = rgba_image.data.copy()
    result = _process(
        "synmachine.image.rotate",
        rgba_image,
        {
            "angle_degrees": 90.0,
            "expand_canvas": True,
            "interpolation": "NEAREST",
            "border_mode": "CONSTANT",
            "border_colour": ColorValue(0.0, 0.0, 0.0, 0.0),
        },
    )
    assert_image_conformance(rgba_image, result, before, expected_shape=(3, 2, 4))
    assert result.data[..., 3].min() >= 0.0


def test_rotate_identity_does_not_sanitize_non_finite_values(rgba_image: ImageFrame) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 0] = np.nan
    data.flags.writeable = False
    source = ImageFrame(
        data,
        rgba_image.color_space,
        rgba_image.channel_names,
        rgba_image.alpha_mode,
        rgba_image.context,
        rgba_image.provenance,
    )
    before = source.data.copy()
    result = _process(
        "synmachine.image.rotate",
        source,
        {"angle_degrees": 0.0, "interpolation": "NEAREST"},
    )
    assert_image_conformance(source, result, before, expected_shape=source.data.shape)
    assert np.isnan(result.data[0, 0, 0])


def test_rotate_expansion_contains_custom_centre_transform(rgb_image: ImageFrame) -> None:
    before = rgb_image.data.copy()
    result = _process(
        "synmachine.image.rotate",
        rgb_image,
        {
            "angle_degrees": 90.0,
            "centre_x": 0.0,
            "centre_y": 0.0,
            "expand_canvas": True,
            "interpolation": "NEAREST",
        },
    )
    assert_image_conformance(rgb_image, result, before, expected_shape=(6, 4, 3))
    assert np.count_nonzero(np.isfinite(result.data)) == result.data.size


def test_batch1_metadata_has_stable_complete_ids() -> None:
    definitions = {item.type_id: item for item in create_image_definitions()}
    assert set(BATCH1_IDS) <= definitions.keys()
    assert [parameter.id for parameter in definitions["synmachine.image.resize"].parameters] == [
        "width",
        "height",
        "preserve_aspect",
        "fit_mode",
        "interpolation",
    ]
    assert [parameter.id for parameter in definitions["synmachine.image.crop"].parameters] == [
        "coordinate_mode",
        "left",
        "top",
        "right",
        "bottom",
        "out_of_bounds",
        "pad_colour",
    ]
    angle = definitions["synmachine.image.rotate"].parameter("angle_degrees")
    assert angle is not None
    assert angle.connectable
