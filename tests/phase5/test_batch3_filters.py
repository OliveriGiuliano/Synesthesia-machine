"""Algorithm, metadata, and conformance tests for Phase 5 Batch 3."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID

import cv2
import numpy as np
import pytest

from synesthesia_machine.contracts import (
    ColorSpace,
    FrameContext,
    ImageFrame,
    ParameterValue,
    read_only_float32,
)
from synesthesia_machine.media import color_space_descriptor
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, NodeDefinition
from synesthesia_machine.nodes.image import create_image_definitions
from tests.phase5.conformance import assert_image_conformance, assert_scheduler_propagates_no_data

NODE_ID = UUID("00000000-0000-0000-0000-000000005040")
BATCH3_IDS = (
    "synmachine.image.gaussian_blur",
    "synmachine.image.sharpen",
    "synmachine.image.add_noise",
    "synmachine.image.posterize",
)
PARAMETER_IDS = {
    "synmachine.image.gaussian_blur": (
        "kernel_width",
        "kernel_height",
        "sigma_x",
        "sigma_y",
        "border_mode",
    ),
    "synmachine.image.sharpen": ("amount", "sigma", "threshold", "border_mode"),
    "synmachine.image.add_noise": (
        "noise_type",
        "amount",
        "seed",
        "monochrome",
        "animate_seed",
        "channels",
    ),
    "synmachine.image.posterize": ("levels", "clamp_input", "channels"),
}
DEFAULTS = {
    "synmachine.image.gaussian_blur": (3, 3, 0.0, 0.0, "REFLECT_101"),
    "synmachine.image.sharpen": (1.0, 1.0, 0.0, "REFLECT_101"),
    "synmachine.image.add_noise": ("GAUSSIAN", 0.05, 0, False, False, "COLOUR"),
    "synmachine.image.posterize": (4, True, "COLOUR"),
}


def _definition(type_id: str) -> NodeDefinition:
    return next(item for item in create_image_definitions() if item.type_id == type_id)


def _process(
    type_id: str,
    image: ImageFrame,
    overrides: Mapping[str, ParameterValue] | None = None,
    *,
    context: FrameContext | None = None,
) -> ImageFrame:
    definition = _definition(type_id)
    parameters, errors = definition.parameter_values(overrides or {})
    assert not errors
    return _runtime_process(definition, image, parameters, context=context)


def _runtime_process(
    definition: NodeDefinition,
    image: ImageFrame,
    parameters: Mapping[str, ParameterValue],
    *,
    context: FrameContext | None = None,
) -> ImageFrame:
    output = definition.runtime_factory(NODE_ID).process(
        {"image": image}, parameters, context or image.context
    )["image"]
    assert isinstance(output, ImageFrame)
    return output


def _image(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    color_space: ColorSpace,
    reference: ImageFrame,
) -> ImageFrame:
    descriptor = color_space_descriptor(color_space)
    return ImageFrame(
        read_only_float32(data),
        color_space,
        descriptor.channel_names,
        descriptor.alpha_mode,
        reference.context,
        reference.provenance,
    )


@pytest.mark.parametrize("type_id", BATCH3_IDS)
def test_batch3_definitions_propagate_no_data(type_id: str) -> None:
    assert_scheduler_propagates_no_data(_definition(type_id), {})


@pytest.mark.parametrize("type_id", BATCH3_IDS)
def test_batch3_defaults_conform_without_mutating_rgba(
    type_id: str, rgba_image: ImageFrame
) -> None:
    before = rgba_image.data.copy()
    result = _process(type_id, rgba_image)
    assert_image_conformance(
        rgba_image,
        result,
        before,
        expected_shape=rgba_image.data.shape,
    )


def test_batch3_metadata_has_stable_ids_defaults_and_non_connectable_parameters() -> None:
    definitions = [item for item in create_image_definitions() if item.type_id in BATCH3_IDS]
    assert tuple(item.type_id for item in definitions) == BATCH3_IDS
    for definition in definitions:
        assert definition.execution_kind is ExecutionKind.STATELESS
        assert definition.category == "Image / Filter"
        assert tuple(port.id for port in definition.inputs) == ("image",)
        assert tuple(port.id for port in definition.outputs) == ("image",)
        assert (
            tuple(parameter.id for parameter in definition.parameters)
            == PARAMETER_IDS[definition.type_id]
        )
        assert (
            tuple(parameter.default for parameter in definition.parameters)
            == DEFAULTS[definition.type_id]
        )
        assert not any(parameter.connectable for parameter in definition.parameters)

    noise = _definition("synmachine.image.add_noise")
    noise_type = noise.parameter("noise_type")
    assert noise_type is not None
    assert noise_type.choices == ("GAUSSIAN", "UNIFORM", "SALT_AND_PEPPER")


def test_gaussian_blur_matches_opencv_sigma_derivation_and_preserves_alpha(
    rgba_image: ImageFrame,
) -> None:
    result = _process("synmachine.image.gaussian_blur", rgba_image)
    expected = cv2.GaussianBlur(
        rgba_image.data[..., :3],
        (3, 3),
        sigmaX=0.0,
        sigmaY=0.0,
        borderType=cv2.BORDER_REFLECT_101,
    )
    assert np.array_equal(result.data[..., :3], expected)
    assert np.array_equal(result.data[..., 3], rgba_image.data[..., 3])


def test_gaussian_blur_honours_rectangular_kernel_sigmas_and_border(rgb_image: ImageFrame) -> None:
    result = _process(
        "synmachine.image.gaussian_blur",
        rgb_image,
        {
            "kernel_width": 5,
            "kernel_height": 3,
            "sigma_x": 1.25,
            "sigma_y": 0.75,
            "border_mode": "REPLICATE",
        },
    )
    expected = cv2.GaussianBlur(
        rgb_image.data,
        (5, 3),
        sigmaX=1.25,
        sigmaY=0.75,
        borderType=cv2.BORDER_REPLICATE,
    )
    assert np.array_equal(result.data, expected)


def test_gaussian_blur_wrap_uses_exact_wrapped_halo(rgb_image: ImageFrame) -> None:
    result = _process(
        "synmachine.image.gaussian_blur",
        rgb_image,
        {"kernel_width": 5, "kernel_height": 3, "border_mode": "WRAP"},
    )
    padded = np.pad(rgb_image.data, ((1, 1), (2, 2), (0, 0)), mode="wrap")
    filtered = cv2.GaussianBlur(
        padded,
        (5, 3),
        sigmaX=0.0,
        sigmaY=0.0,
        borderType=cv2.BORDER_CONSTANT,
    )
    assert np.array_equal(result.data, filtered[1:-1, 2:-2])


def test_gaussian_blur_keeps_native_non_finite_propagation_and_non_finite_alpha(
    rgba_image: ImageFrame,
) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 0] = np.nan
    data[0, 0, 3] = np.inf
    source = _image(data, ColorSpace.RGBA, rgba_image)
    result = _process("synmachine.image.gaussian_blur", source)
    expected = cv2.GaussianBlur(
        source.data[..., :3],
        (3, 3),
        sigmaX=0.0,
        sigmaY=0.0,
        borderType=cv2.BORDER_REFLECT_101,
    )
    assert np.array_equal(result.data[..., :3], expected, equal_nan=True)
    assert np.isposinf(result.data[0, 0, 3])


def test_sharpen_implements_unsharp_mask_without_clipping_and_preserves_alpha(
    rgba_image: ImageFrame,
) -> None:
    result = _process(
        "synmachine.image.sharpen",
        rgba_image,
        {"amount": 2.0, "sigma": 1.0},
    )
    colour = rgba_image.data[..., :3]
    blur = cv2.GaussianBlur(
        colour,
        (0, 0),
        sigmaX=1.0,
        sigmaY=1.0,
        borderType=cv2.BORDER_REFLECT_101,
    )
    expected = colour + np.float32(2.0) * (colour - blur)
    assert np.array_equal(result.data[..., :3], expected)
    assert np.array_equal(result.data[..., 3], rgba_image.data[..., 3])
    assert np.any(result.data[..., :3] < 0.0) or np.any(result.data[..., :3] > 1.0)


def test_sharpen_threshold_suppresses_small_differences_and_amount_can_be_negative(
    rgb_image: ImageFrame,
) -> None:
    source = _image(
        np.array(
            [
                [[0.0, 0.1, 0.2], [0.3, 0.4, 0.5], [0.6, 0.7, 0.8]],
                [[0.2, 0.3, 0.4], [1.0, 0.9, 0.8], [0.4, 0.3, 0.2]],
                [[0.8, 0.7, 0.6], [0.5, 0.4, 0.3], [0.2, 0.1, 0.0]],
            ],
            dtype=np.float32,
        ),
        ColorSpace.SRGB,
        rgb_image,
    )
    result = _process(
        "synmachine.image.sharpen",
        source,
        {"amount": -0.5, "sigma": 0.75, "threshold": 0.1},
    )
    blur = cv2.GaussianBlur(
        source.data,
        (0, 0),
        sigmaX=0.75,
        sigmaY=0.75,
        borderType=cv2.BORDER_REFLECT_101,
    )
    detail = source.data - blur
    detail = np.where(np.abs(detail) >= np.float32(0.1), detail, np.float32(0.0))
    expected = source.data + np.float32(-0.5) * detail
    assert np.array_equal(result.data, expected)


def test_sharpen_wrap_matches_wrapped_automatic_gaussian(rgb_image: ImageFrame) -> None:
    result = _process(
        "synmachine.image.sharpen",
        rgb_image,
        {"sigma": 1.0, "border_mode": "WRAP"},
    )
    padded = np.pad(rgb_image.data, ((4, 4), (4, 4), (0, 0)), mode="wrap")
    blur = cv2.GaussianBlur(
        padded,
        (0, 0),
        sigmaX=1.0,
        sigmaY=1.0,
        borderType=cv2.BORDER_CONSTANT,
    )[4:-4, 4:-4]
    assert np.array_equal(result.data, rgb_image.data + (rgb_image.data - blur))


@pytest.mark.parametrize("noise_type", ["GAUSSIAN", "UNIFORM", "SALT_AND_PEPPER"])
def test_noise_is_repeatable_for_seed_and_input_when_animation_is_disabled(
    noise_type: str, rgb_image: ImageFrame
) -> None:
    parameters: dict[str, ParameterValue] = {
        "noise_type": noise_type,
        "amount": 0.25,
        "seed": 41,
    }
    first = _process(
        "synmachine.image.add_noise",
        rgb_image,
        parameters,
        context=replace(rgb_image.context, tick_index=3),
    )
    second = _process(
        "synmachine.image.add_noise",
        rgb_image,
        parameters,
        context=replace(rgb_image.context, tick_index=99),
    )
    assert np.array_equal(first.data, second.data)


def test_gaussian_noise_matches_seeded_float32_generator_without_clipping(
    rgb_image: ImageFrame,
) -> None:
    result = _process(
        "synmachine.image.add_noise",
        rgb_image,
        {"noise_type": "GAUSSIAN", "amount": 0.5, "seed": 13},
    )
    rng = np.random.default_rng(np.random.SeedSequence(13))
    expected = rgb_image.data + rng.standard_normal(
        rgb_image.data.shape, dtype=np.float32
    ) * np.float32(0.5)
    assert np.array_equal(result.data, expected)
    assert np.any(result.data < 0.0) or np.any(result.data > 1.0)


def test_uniform_noise_matches_seeded_symmetric_range(rgb_image: ImageFrame) -> None:
    result = _process(
        "synmachine.image.add_noise",
        rgb_image,
        {"noise_type": "UNIFORM", "amount": 0.2, "seed": -3},
    )
    normalized_seed = -3 & ((1 << 64) - 1)
    rng = np.random.default_rng(np.random.SeedSequence(normalized_seed))
    noise = rng.random(rgb_image.data.shape, dtype=np.float32)
    noise = (noise * np.float32(2.0) - np.float32(1.0)) * np.float32(0.2)
    assert np.array_equal(result.data, rgb_image.data + noise)
    assert np.all(noise >= np.float32(-0.2))
    assert np.all(noise < np.float32(0.2))


def test_salt_and_pepper_uses_amount_as_total_replacement_probability(
    rgb_image: ImageFrame,
) -> None:
    source = _image(np.full((5, 7, 3), 0.25, dtype=np.float32), ColorSpace.SRGB, rgb_image)
    result = _process(
        "synmachine.image.add_noise",
        source,
        {"noise_type": "SALT_AND_PEPPER", "amount": 1.0, "seed": 9},
    )
    assert set(np.unique(result.data).tolist()) == {0.0, 1.0}
    assert np.count_nonzero(result.data == 0.0) > 0
    assert np.count_nonzero(result.data == 1.0) > 0


def test_noise_monochrome_and_independent_channel_shapes(rgb_image: ImageFrame) -> None:
    source = _image(np.zeros((4, 6, 3), dtype=np.float32), ColorSpace.SRGB, rgb_image)
    monochrome = _process(
        "synmachine.image.add_noise",
        source,
        {"amount": 0.5, "seed": 2, "monochrome": True},
    )
    assert np.array_equal(monochrome.data[..., 0], monochrome.data[..., 1])
    assert np.array_equal(monochrome.data[..., 1], monochrome.data[..., 2])

    independent = _process(
        "synmachine.image.add_noise",
        source,
        {"amount": 0.5, "seed": 2, "monochrome": False},
    )
    assert not np.array_equal(independent.data[..., 0], independent.data[..., 1])


def test_noise_animation_uses_deterministic_tick_derived_seed(rgb_image: ImageFrame) -> None:
    parameters: dict[str, ParameterValue] = {
        "amount": 0.5,
        "seed": 23,
        "animate_seed": True,
    }
    tick_8_a = _process(
        "synmachine.image.add_noise",
        rgb_image,
        parameters,
        context=replace(rgb_image.context, tick_index=8),
    )
    tick_8_b = _process(
        "synmachine.image.add_noise",
        rgb_image,
        parameters,
        context=replace(rgb_image.context, tick_index=8),
    )
    tick_9 = _process(
        "synmachine.image.add_noise",
        rgb_image,
        parameters,
        context=replace(rgb_image.context, tick_index=9),
    )
    assert np.array_equal(tick_8_a.data, tick_8_b.data)
    assert not np.array_equal(tick_8_a.data, tick_9.data)


def test_noise_channel_selection_preserves_unselected_channels_and_alpha(
    rgba_image: ImageFrame,
) -> None:
    result = _process(
        "synmachine.image.add_noise",
        rgba_image,
        {"amount": 0.5, "seed": 5, "channels": "CHANNEL_2"},
    )
    assert np.array_equal(result.data[..., (0, 2, 3)], rgba_image.data[..., (0, 2, 3)])
    assert not np.array_equal(result.data[..., 1], rgba_image.data[..., 1])

    all_channels = _process(
        "synmachine.image.add_noise",
        rgba_image,
        {"amount": 0.5, "seed": 5, "channels": "ALL"},
    )
    assert not np.array_equal(all_channels.data[..., 3], rgba_image.data[..., 3])


@pytest.mark.parametrize("noise_type", ["GAUSSIAN", "UNIFORM", "SALT_AND_PEPPER"])
def test_noise_preserves_existing_non_finite_selected_values(
    noise_type: str, rgba_image: ImageFrame
) -> None:
    source = _image(
        np.array([[[np.nan, np.inf, -np.inf, np.nan]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    result = _process(
        "synmachine.image.add_noise",
        source,
        {"noise_type": noise_type, "amount": 0.5, "seed": 1},
    )
    assert np.isnan(result.data[0, 0, 0])
    assert np.isposinf(result.data[0, 0, 1])
    assert np.isneginf(result.data[0, 0, 2])
    assert np.isnan(result.data[0, 0, 3])


def test_posterize_applies_documented_formula_and_preserves_alpha(
    rgba_image: ImageFrame,
) -> None:
    source = _image(
        np.array([[[0.1, 0.4, 0.8, 0.37]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    result = _process("synmachine.image.posterize", source, {"levels": 4})
    assert np.allclose(result.data[0, 0, :3], (0.0, 1.0 / 3.0, 2.0 / 3.0))
    assert result.data[0, 0, 3] == pytest.approx(0.37)


def test_posterize_optional_clamp_controls_out_of_range_and_infinity_semantics(
    rgb_image: ImageFrame,
) -> None:
    source = _image(
        np.array([[[-0.2, 1.2, np.nan], [np.inf, -np.inf, 0.4]]], dtype=np.float32),
        ColorSpace.SRGB,
        rgb_image,
    )
    clamped = _process("synmachine.image.posterize", source, {"levels": 4})
    assert clamped.data[0, 0, 0] == 0.0
    assert clamped.data[0, 0, 1] == 1.0
    assert np.isnan(clamped.data[0, 0, 2])
    assert clamped.data[0, 1, 0] == 1.0
    assert clamped.data[0, 1, 1] == 0.0

    unclamped = _process(
        "synmachine.image.posterize",
        source,
        {"levels": 4, "clamp_input": False},
    )
    assert unclamped.data[0, 0, 0] == pytest.approx(-1.0 / 3.0)
    assert unclamped.data[0, 0, 1] == pytest.approx(4.0 / 3.0)
    assert np.isnan(unclamped.data[0, 0, 2])
    assert np.isposinf(unclamped.data[0, 1, 0])
    assert np.isneginf(unclamped.data[0, 1, 1])


def test_posterize_channel_selection_can_process_alpha(rgba_image: ImageFrame) -> None:
    selected = _process(
        "synmachine.image.posterize",
        rgba_image,
        {"levels": 2, "channels": "CHANNEL_1"},
    )
    assert np.array_equal(selected.data[..., 1:], rgba_image.data[..., 1:])

    all_channels = _process(
        "synmachine.image.posterize",
        rgba_image,
        {"levels": 2, "channels": "ALL"},
    )
    assert set(np.unique(all_channels.data[..., 3]).tolist()) <= {0.0, 1.0}


def test_filter_channel_selection_errors_are_recoverable(rgb_image: ImageFrame) -> None:
    for type_id in ("synmachine.image.add_noise", "synmachine.image.posterize"):
        with pytest.raises(ExpectedNodeError, match="CHANNEL_4 is unavailable"):
            _process(type_id, rgb_image, {"channels": "CHANNEL_4"})


@pytest.mark.parametrize(
    ("type_id", "overrides", "message"),
    [
        ("synmachine.image.gaussian_blur", {"kernel_width": 2}, "positive odd"),
        ("synmachine.image.gaussian_blur", {"sigma_x": float("nan")}, "sigma_x must be finite"),
        ("synmachine.image.sharpen", {"amount": float("inf")}, "amount must be finite"),
        ("synmachine.image.sharpen", {"sigma": 0.0}, "sigma must be positive"),
        ("synmachine.image.sharpen", {"threshold": -0.1}, "threshold"),
        ("synmachine.image.add_noise", {"amount": float("nan")}, "amount must be finite"),
        (
            "synmachine.image.add_noise",
            {"noise_type": "SALT_AND_PEPPER", "amount": 1.1},
            "must not exceed 1",
        ),
        ("synmachine.image.posterize", {"levels": 1}, "at least 2"),
    ],
)
def test_definition_validators_reject_invalid_filter_literals(
    type_id: str,
    overrides: Mapping[str, object],
    message: str,
) -> None:
    _, errors = _definition(type_id).parameter_values(overrides)
    assert any(message in error for error in errors)


@pytest.mark.parametrize(
    ("type_id", "mutations", "message"),
    [
        ("synmachine.image.gaussian_blur", {"kernel_height": 4}, "positive odd"),
        ("synmachine.image.gaussian_blur", {"sigma_y": -1.0}, "non-negative"),
        ("synmachine.image.sharpen", {"sigma": 0.0}, "positive"),
        ("synmachine.image.sharpen", {"threshold": float("nan")}, "non-negative"),
        ("synmachine.image.add_noise", {"amount": -1.0}, "non-negative"),
        ("synmachine.image.posterize", {"levels": 1}, "at least 2"),
        ("synmachine.image.posterize", {"channels": "NOT_A_CHANNEL"}, "not a valid"),
    ],
)
def test_malformed_runtime_parameters_produce_recoverable_errors(
    type_id: str,
    mutations: Mapping[str, ParameterValue],
    message: str,
    rgb_image: ImageFrame,
) -> None:
    definition = _definition(type_id)
    parameters, errors = definition.parameter_values({})
    assert not errors
    parameters.update(mutations)
    with pytest.raises(ExpectedNodeError, match=message):
        _runtime_process(definition, rgb_image, parameters)
