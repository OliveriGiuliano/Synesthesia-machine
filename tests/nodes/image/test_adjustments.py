"""Image adjustment algorithm, metadata, and conformance tests."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np
import pytest
from tests.support.image_conformance import (
    assert_image_conformance,
    assert_scheduler_propagates_no_data,
)

from synesthesia_machine.contracts import (
    ColorSpace,
    ImageFrame,
    ParameterValue,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media import color_space_descriptor
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    NodeDefinition,
    ParameterEditorHint,
)
from synesthesia_machine.nodes.image import create_image_definitions

NODE_ID = UUID("00000000-0000-0000-0000-000000005030")
BATCH2_IDS = (
    "synmachine.image.brightness",
    "synmachine.image.contrast",
    "synmachine.image.clamp",
    "synmachine.image.colour_levels",
    "synmachine.image.hue",
    "synmachine.image.saturation",
    "synmachine.image.invert_colour",
    "synmachine.image.stretch_contrast",
    "synmachine.image.gamma",
    "synmachine.image.add_scalar",
    "synmachine.image.multiply_scalar",
    "synmachine.image.divide_scalar",
)

PARAMETER_IDS = {
    "synmachine.image.brightness": ("offset", "channels"),
    "synmachine.image.contrast": ("factor", "pivot", "channels"),
    "synmachine.image.clamp": ("minimum", "maximum", "channels"),
    "synmachine.image.colour_levels": (
        "input_black",
        "input_white",
        "gamma",
        "output_black",
        "output_white",
        "channels",
    ),
    "synmachine.image.hue": ("turns",),
    "synmachine.image.saturation": ("factor",),
    "synmachine.image.invert_colour": (),
    "synmachine.image.stretch_contrast": (
        "mode",
        "lower_percentile",
        "upper_percentile",
        "ignore_non_finite",
        "constant_policy",
        "channels",
    ),
    "synmachine.image.gamma": ("gamma", "channels"),
    "synmachine.image.add_scalar": ("value", "channels"),
    "synmachine.image.multiply_scalar": ("value", "channels"),
    "synmachine.image.divide_scalar": ("value", "near_zero_policy", "epsilon", "channels"),
}

CONNECTABLE_IDS: Mapping[str, frozenset[str]] = {
    "synmachine.image.brightness": frozenset({"offset"}),
    "synmachine.image.contrast": frozenset({"factor", "pivot"}),
    "synmachine.image.clamp": frozenset({"minimum", "maximum"}),
    "synmachine.image.colour_levels": frozenset(
        {
            "input_black",
            "input_white",
            "gamma",
            "output_black",
            "output_white",
        }
    ),
    "synmachine.image.hue": frozenset({"turns"}),
    "synmachine.image.saturation": frozenset({"factor"}),
    "synmachine.image.invert_colour": frozenset(),
    "synmachine.image.stretch_contrast": frozenset(
        {"lower_percentile", "upper_percentile", "ignore_non_finite"}
    ),
    "synmachine.image.gamma": frozenset({"gamma"}),
    "synmachine.image.add_scalar": frozenset({"value"}),
    "synmachine.image.multiply_scalar": frozenset({"value"}),
    "synmachine.image.divide_scalar": frozenset({"value", "epsilon"}),
}


def _definition(type_id: str) -> NodeDefinition:
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


@pytest.mark.parametrize("type_id", BATCH2_IDS)
def test_batch2_definitions_propagate_no_data(type_id: str) -> None:
    assert_scheduler_propagates_no_data(_definition(type_id), {})


@pytest.mark.parametrize("type_id", BATCH2_IDS)
def test_batch2_defaults_conform_without_mutating_rgba(
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


def test_batch2_metadata_has_stable_complete_ids_and_connectability() -> None:
    definitions = [item for item in create_image_definitions() if item.type_id in BATCH2_IDS]
    assert tuple(item.type_id for item in definitions) == BATCH2_IDS
    for definition in definitions:
        assert definition.execution_kind is ExecutionKind.STATELESS
        assert definition.category == "Image / Adjustment"
        assert tuple(port.id for port in definition.inputs) == ("image",)
        assert tuple(port.id for port in definition.outputs) == ("image",)
        assert (
            tuple(parameter.id for parameter in definition.parameters)
            == PARAMETER_IDS[definition.type_id]
        )
        assert {
            parameter.id for parameter in definition.parameters if parameter.connectable
        } == CONNECTABLE_IDS[definition.type_id]

    hue = _definition("synmachine.image.hue")
    turns = hue.parameter("turns")
    assert hue.implementation_version == 2
    assert turns is not None
    assert (turns.minimum, turns.maximum) == (0.0, 1.0)
    assert turns.editor_hint is ParameterEditorHint.SLIDER


def test_brightness_uses_connected_offset_and_selected_channel(rgba_image: ImageFrame) -> None:
    before = rgba_image.data.copy()
    result = _process(
        "synmachine.image.brightness",
        rgba_image,
        {"offset": 10.0, "channels": "CHANNEL_2"},
        {"offset": 0.25},
    )
    expected = before.copy()
    expected[..., 1] += np.float32(0.25)
    assert np.array_equal(result.data, expected)
    assert np.array_equal(result.data[..., 3], before[..., 3])


def test_contrast_applies_factor_around_pivot_without_clipping(rgba_image: ImageFrame) -> None:
    result = _process(
        "synmachine.image.contrast",
        rgba_image,
        {"factor": 2.0, "pivot": 0.25},
    )
    expected_colour = (rgba_image.data[..., :3] - np.float32(0.25)) * np.float32(2.0) + np.float32(
        0.25
    )
    assert np.allclose(result.data[..., :3], expected_colour)
    assert np.array_equal(result.data[..., 3], rgba_image.data[..., 3])
    assert result.data[..., :3].min() < 0.0


def test_clamp_preserves_alpha_and_has_explicit_non_finite_semantics(
    rgba_image: ImageFrame,
) -> None:
    source = _image(
        np.array([[[np.nan, np.inf, -np.inf, 2.0]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    preserved = _process("synmachine.image.clamp", source)
    assert np.isnan(preserved.data[0, 0, 0])
    assert np.array_equal(preserved.data[0, 0, 1:3], (1.0, 0.0))
    assert np.array_equal(preserved.data[0, 0, 3], 2.0)


def test_colour_levels_applies_normalization_gamma_and_output_range(
    rgba_image: ImageFrame,
) -> None:
    source = _image(
        np.array([[[0.25, 0.5, 1.0, 0.4]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    result = _process(
        "synmachine.image.colour_levels",
        source,
        {
            "input_black": 0.0,
            "input_white": 1.0,
            "gamma": 2.0,
            "output_black": 0.2,
            "output_white": 0.8,
        },
    )
    expected = np.sqrt(source.data[..., :3]) * np.float32(0.6) + np.float32(0.2)
    assert np.allclose(result.data[..., :3], expected, atol=1e-6)
    assert result.data[0, 0, 3] == pytest.approx(0.4)


def test_colour_levels_clamps_out_of_range_inputs_before_fractional_gamma(
    rgba_image: ImageFrame,
) -> None:
    source = _image(
        np.array([[[0.2, 0.94, 1.8, 0.4]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )

    result = _process(
        "synmachine.image.colour_levels",
        source,
        {
            "input_black": 0.3,
            "input_white": 1.58,
            "gamma": 1.18,
            "output_black": 0.1,
            "output_white": 0.9,
        },
    )

    normalized = np.clip(
        (source.data[..., :3] - np.float32(0.3)) / np.float32(1.58 - 0.3),
        np.float32(0.0),
        np.float32(1.0),
    )
    expected = np.power(normalized, np.float32(1.0 / 1.18)) * np.float32(0.8) + np.float32(0.1)
    assert np.isfinite(result.data[..., :3]).all()
    assert np.allclose(result.data[..., :3], expected, atol=1e-6)
    assert result.data[0, 0, 3] == pytest.approx(0.4)


def test_hue_and_saturation_convert_rgba_and_preserve_alpha(rgba_image: ImageFrame) -> None:
    red = _image(
        np.array([[[1.0, 0.0, 0.0, 0.25]]], dtype=np.float32),
        ColorSpace.RGBA,
        rgba_image,
    )
    shifted = _process("synmachine.image.hue", red, {"turns": 1.0 / 3.0})
    assert np.allclose(shifted.data[0, 0, :3], (0.0, 1.0, 0.0), atol=2e-5)
    assert shifted.data[0, 0, 3] == 0.25

    desaturated = _process("synmachine.image.saturation", red, {"factor": 0.0})
    assert np.allclose(desaturated.data[0, 0, :3], (1.0, 1.0, 1.0), atol=2e-5)
    assert desaturated.data[0, 0, 3] == 0.25


@pytest.mark.parametrize("color_space", [ColorSpace.HSV, ColorSpace.HSL])
def test_hue_and_saturation_use_direct_hsv_hsl_semantics(
    color_space: ColorSpace, rgb_image: ImageFrame
) -> None:
    source = _image(
        np.array([[[0.9, 0.25, 0.75]]], dtype=np.float32),
        color_space,
        rgb_image,
    )
    shifted = _process("synmachine.image.hue", source, {"turns": 0.2})
    assert np.allclose(shifted.data, [[[0.1, 0.25, 0.75]]], atol=1e-6)
    saturated = _process("synmachine.image.saturation", source, {"factor": 2.0})
    assert np.allclose(saturated.data, [[[0.9, 0.5, 0.75]]], atol=1e-6)


@pytest.mark.parametrize("type_id", ["synmachine.image.hue", "synmachine.image.saturation"])
def test_hue_and_saturation_reject_non_finite_colour_as_recoverable(
    type_id: str, rgba_image: ImageFrame
) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 0] = np.nan
    source = _image(data, ColorSpace.RGBA, rgba_image)
    with pytest.raises(ExpectedNodeError, match="finite colour-channel"):
        _process(type_id, source)


def test_hue_allows_and_preserves_non_finite_alpha(rgba_image: ImageFrame) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 3] = np.nan
    source = _image(data, ColorSpace.RGBA, rgba_image)
    result = _process("synmachine.image.hue", source, {"turns": 0.25})
    assert np.isnan(result.data[0, 0, 3])


def test_invert_colour_preserves_alpha(rgba_image: ImageFrame) -> None:
    preserved = _process("synmachine.image.invert_colour", rgba_image)
    assert np.allclose(preserved.data[..., :3], 1.0 - rgba_image.data[..., :3])
    assert np.array_equal(preserved.data[..., 3], rgba_image.data[..., 3])


def test_invert_colour_rejects_non_normalized_descriptors(rgb_image: ImageFrame) -> None:
    lab = _image(np.array([[[50.0, 0.0, 0.0]]], dtype=np.float32), ColorSpace.LAB, rgb_image)
    with pytest.raises(ExpectedNodeError, match=r"normalized 0\.\.1"):
        _process("synmachine.image.invert_colour", lab)


def test_stretch_contrast_supports_per_channel_and_combined_modes(rgb_image: ImageFrame) -> None:
    source = _image(
        np.array([[[0.0, 10.0, 20.0], [1.0, 11.0, 21.0]]], dtype=np.float32),
        ColorSpace.SRGB,
        rgb_image,
    )
    per_channel = _process("synmachine.image.stretch_contrast", source)
    assert np.allclose(per_channel.data, [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]])

    combined = _process(
        "synmachine.image.stretch_contrast",
        source,
        {"mode": "COMBINED"},
    )
    assert np.allclose(combined.data, source.data / np.float32(21.0))


def test_stretch_contrast_uses_percentiles_and_preserves_ignored_non_finite_positions(
    rgb_image: ImageFrame,
) -> None:
    source = _image(
        np.array(
            [[[0.0, 2.0, 3.0], [1.0, 2.0, 3.0], [100.0, 2.0, 3.0], [np.nan, 2.0, 3.0]]],
            dtype=np.float32,
        ),
        ColorSpace.SRGB,
        rgb_image,
    )
    result = _process(
        "synmachine.image.stretch_contrast",
        source,
        {"upper_percentile": 50.0, "channels": "CHANNEL_1"},
    )
    assert np.array_equal(result.data[0, :3, 0], (0.0, 1.0, 1.0))
    assert np.isnan(result.data[0, 3, 0])
    assert np.array_equal(result.data[..., 1:], source.data[..., 1:])

    with pytest.raises(ExpectedNodeError, match="must be finite"):
        _process(
            "synmachine.image.stretch_contrast",
            source,
            {"ignore_non_finite": False, "channels": "CHANNEL_1"},
        )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("PRESERVE", 7.0), ("ZERO", 0.0), ("MIDPOINT", 0.5)],
)
def test_stretch_contrast_constant_channel_policies(
    policy: str, expected: float, rgb_image: ImageFrame
) -> None:
    source = _image(np.full((2, 2, 3), 7.0, dtype=np.float32), ColorSpace.SRGB, rgb_image)
    result = _process(
        "synmachine.image.stretch_contrast",
        source,
        {"constant_policy": policy, "channels": "CHANNEL_1"},
    )
    assert np.all(result.data[..., 0] == np.float32(expected))
    assert np.array_equal(result.data[..., 1:], source.data[..., 1:])


def test_stretch_contrast_rejects_selection_without_finite_samples(rgb_image: ImageFrame) -> None:
    source = _image(
        np.array([[[np.nan, 0.0, 0.0], [np.inf, 1.0, 1.0]]], dtype=np.float32),
        ColorSpace.SRGB,
        rgb_image,
    )
    with pytest.raises(ExpectedNodeError, match="no finite selected samples"):
        _process(
            "synmachine.image.stretch_contrast",
            source,
            {"channels": "CHANNEL_1"},
        )


def test_gamma_clamps_negative_selected_values_and_preserves_ieee_values(
    rgba_image: ImageFrame,
) -> None:
    source = _image(
        np.array(
            [[[-1.0, 0.5, np.nan, 0.4], [np.inf, 0.25, 1.0, 0.8]]],
            dtype=np.float32,
        ),
        ColorSpace.RGBA,
        rgba_image,
    )
    result = _process("synmachine.image.gamma", source, {"gamma": 2.0})
    assert result.data[0, 0, 0] == 0.0
    assert result.data[0, 0, 1] == pytest.approx(0.25)
    assert np.isnan(result.data[0, 0, 2])
    assert np.isposinf(result.data[0, 1, 0])
    assert np.array_equal(result.data[..., 3], source.data[..., 3])


def test_scalar_arithmetic_uses_selected_channels(
    rgba_image: ImageFrame,
) -> None:
    added = _process(
        "synmachine.image.add_scalar",
        rgba_image,
        {"value": 0.5, "channels": "CHANNEL_1"},
    )
    assert np.allclose(added.data[..., 0], rgba_image.data[..., 0] + np.float32(0.5))
    assert np.array_equal(added.data[..., 1:], rgba_image.data[..., 1:])

    multiplied = _process(
        "synmachine.image.multiply_scalar",
        rgba_image,
        {"value": 2.0, "channels": "ALL"},
    )
    assert np.allclose(multiplied.data, rgba_image.data * np.float32(2.0))

    divided = _process(
        "synmachine.image.divide_scalar",
        rgba_image,
        {"value": 2.0, "channels": "CHANNEL_3"},
    )
    assert np.allclose(divided.data[..., 2], rgba_image.data[..., 2] / np.float32(2.0))
    assert np.array_equal(divided.data[..., (0, 1, 3)], rgba_image.data[..., (0, 1, 3)])


def test_divide_scalar_near_zero_policies_are_explicit(rgb_image: ImageFrame) -> None:
    replaced = _process(
        "synmachine.image.divide_scalar",
        rgb_image,
        {"value": 0.0},
    )
    assert np.all(replaced.data == 0.0)

    clamped = _process(
        "synmachine.image.divide_scalar",
        rgb_image,
        {"value": -0.01, "epsilon": 0.1, "near_zero_policy": "CLAMP_EPSILON"},
    )
    assert np.allclose(clamped.data, rgb_image.data / np.float32(-0.1))

    with pytest.raises(ExpectedNodeError, match="within epsilon"):
        _process(
            "synmachine.image.divide_scalar",
            rgb_image,
            {"value": 0.0, "near_zero_policy": "ERROR"},
        )


def test_retired_channel_selection_is_rejected_at_validation() -> None:
    # CHANNEL_4 is no longer an offered choice; the literal is rejected and
    # the parameter falls back to its COLOUR default.
    values, errors = _definition("synmachine.image.brightness").parameter_values(
        {"channels": "CHANNEL_4"}
    )
    assert len(errors) == 1
    assert "expected one of" in errors[0]
    assert values["channels"] == "COLOUR"


@pytest.mark.parametrize(
    ("type_id", "overrides", "message"),
    [
        ("synmachine.image.brightness", {"offset": float("nan")}, "offset must be finite"),
        (
            "synmachine.image.colour_levels",
            {"output_black": float("inf")},
            "output_black must be finite",
        ),
        (
            "synmachine.image.colour_levels",
            {"input_black": 1.0, "input_white": 1.0},
            "input white must be greater",
        ),
        ("synmachine.image.clamp", {"minimum": 2.0, "maximum": 1.0}, "maximum must be"),
        (
            "synmachine.image.stretch_contrast",
            {"lower_percentile": 50.0, "upper_percentile": 50.0},
            "percentiles must satisfy",
        ),
        ("synmachine.image.gamma", {"gamma": 0.0}, "at least"),
        ("synmachine.image.divide_scalar", {"epsilon": 0.0}, "at least"),
        ("synmachine.image.saturation", {"factor": float("nan")}, "factor must be finite"),
    ],
)
def test_definition_validators_reject_invalid_literals(
    type_id: str, overrides: Mapping[str, object], message: str
) -> None:
    _, errors = _definition(type_id).parameter_values(overrides)
    assert any(message in error for error in errors)


@pytest.mark.parametrize("type_id", ["synmachine.image.saturation"])
def test_non_negative_factor_definitions_accept_zero(type_id: str) -> None:
    _, errors = _definition(type_id).parameter_values({"factor": 0.0})
    assert not errors


@pytest.mark.parametrize(
    ("type_id", "connected", "message"),
    [
        ("synmachine.image.saturation", {"factor": -1.0}, "non-negative"),
        ("synmachine.image.gamma", {"gamma": 0.0}, "positive"),
        (
            "synmachine.image.colour_levels",
            {"input_white": 0.0},
            "input white must be greater",
        ),
    ],
)
def test_connected_invalid_scalars_are_recoverable_at_runtime(
    type_id: str,
    connected: Mapping[str, RuntimeValue],
    message: str,
    rgb_image: ImageFrame,
) -> None:
    with pytest.raises(ExpectedNodeError, match=message):
        _process(type_id, rgb_image, connected=connected)
