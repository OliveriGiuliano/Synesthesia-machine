"""Image analysis algorithm, metadata, and conformance tests."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import cv2
import numpy as np
import pytest
from tests.support.image_conformance import (
    assert_channel_conformance,
    assert_image_conformance,
    assert_scheduler_propagates_no_data,
)

from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameContext,
    ImageFrame,
    NumericMatrix,
    ParameterValue,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media import color_space_descriptor, image_to_luminance
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, NodeDefinition
from synesthesia_machine.nodes.image import create_image_definitions

NODE_ID = UUID("00000000-0000-0000-0000-000000005050")
BATCH4_IDS = (
    "synmachine.image.threshold",
    "synmachine.image.canny",
    "synmachine.image.convolve",
    "synmachine.image.dilate",
    "synmachine.image.erode",
    "synmachine.image.high_pass",
    "synmachine.image.low_pass",
)
IMAGE_OUTPUT_IDS = BATCH4_IDS[2:]
PARAMETER_IDS = {
    "synmachine.image.threshold": ("mode", "threshold", "maximum"),
    "synmachine.image.canny": (
        "low_threshold",
        "high_threshold",
        "aperture_size",
        "l2_gradient",
        "pre_blur_sigma",
    ),
    "synmachine.image.convolve": (
        "kernel",
        "normalization",
        "scale",
        "delta",
        "border_mode",
    ),
    "synmachine.image.dilate": (
        "kernel_shape",
        "kernel_width",
        "kernel_height",
        "iterations",
        "anchor_x",
        "anchor_y",
        "border_mode",
        "process_alpha",
    ),
    "synmachine.image.erode": (
        "kernel_shape",
        "kernel_width",
        "kernel_height",
        "iterations",
        "anchor_x",
        "anchor_y",
        "border_mode",
        "process_alpha",
    ),
    "synmachine.image.high_pass": ("sigma", "display_offset", "gain", "border_mode"),
    "synmachine.image.low_pass": ("sigma", "border_mode"),
}
MORPH_DEFAULTS = ("RECTANGLE", 3, 3, 1, -1, -1, "REFLECT_101", False)
DEFAULTS = {
    "synmachine.image.threshold": ("BINARY", 0.5, 1.0),
    "synmachine.image.canny": (0.1, 0.3, 3, False, 0.0),
    "synmachine.image.convolve": (
        NumericMatrix(((1.0,),)),
        "NONE",
        1.0,
        0.0,
        "REFLECT_101",
    ),
    "synmachine.image.dilate": MORPH_DEFAULTS,
    "synmachine.image.erode": MORPH_DEFAULTS,
    "synmachine.image.high_pass": (1.0, 0.0, 1.0, "REFLECT_101"),
    "synmachine.image.low_pass": (1.0, "REFLECT_101"),
}
CONNECTABLE_IDS = {
    "synmachine.image.threshold": {"threshold", "maximum"},
    "synmachine.image.canny": {
        "low_threshold",
        "high_threshold",
        "aperture_size",
        "l2_gradient",
        "pre_blur_sigma",
    },
    "synmachine.image.convolve": {"scale", "delta"},
    "synmachine.image.dilate": {
        "kernel_width",
        "kernel_height",
        "iterations",
        "anchor_x",
        "anchor_y",
        "process_alpha",
    },
    "synmachine.image.erode": {
        "kernel_width",
        "kernel_height",
        "iterations",
        "anchor_x",
        "anchor_y",
        "process_alpha",
    },
    "synmachine.image.high_pass": {"sigma", "display_offset", "gain"},
    "synmachine.image.low_pass": {"sigma"},
}
CV_THRESHOLDS = {
    "BINARY": cv2.THRESH_BINARY,
    "BINARY_INVERSE": cv2.THRESH_BINARY_INV,
    "TRUNCATE": cv2.THRESH_TRUNC,
    "TO_ZERO": cv2.THRESH_TOZERO,
    "TO_ZERO_INVERSE": cv2.THRESH_TOZERO_INV,
}
CV_MORPH_SHAPES = {
    "RECTANGLE": cv2.MORPH_RECT,
    "ELLIPSE": cv2.MORPH_ELLIPSE,
    "CROSS": cv2.MORPH_CROSS,
}


def _definition(type_id: str) -> NodeDefinition:
    return next(item for item in create_image_definitions() if item.type_id == type_id)


def _parameters(
    definition: NodeDefinition,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, ParameterValue]:
    parameters, errors = definition.parameter_values(overrides or {})
    assert not errors
    return parameters


def _runtime_process(
    definition: NodeDefinition,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> Mapping[str, RuntimeValue]:
    return definition.runtime_factory(NODE_ID).process(inputs, parameters, context)


def _process_image(
    type_id: str,
    image: ImageFrame,
    overrides: Mapping[str, object] | None = None,
) -> ImageFrame:
    definition = _definition(type_id)
    result = _runtime_process(
        definition,
        {"image": image},
        _parameters(definition, overrides),
        image.context,
    )["image"]
    assert isinstance(result, ImageFrame)
    return result


def _process_channel(
    channel: ChannelFrame,
    overrides: Mapping[str, object] | None = None,
    *,
    connected: Mapping[str, RuntimeValue] | None = None,
) -> ChannelFrame:
    definition = _definition("synmachine.image.threshold")
    inputs: dict[str, RuntimeValue] = {"channel": channel}
    inputs.update(connected or {})
    result = _runtime_process(
        definition,
        inputs,
        _parameters(definition, overrides),
        channel.context,
    )["channel"]
    assert isinstance(result, ChannelFrame)
    return result


def _process_canny(
    image: ImageFrame,
    overrides: Mapping[str, object] | None = None,
) -> ChannelFrame:
    definition = _definition("synmachine.image.canny")
    result = _runtime_process(
        definition,
        {"image": image},
        _parameters(definition, overrides),
        image.context,
    )["channel"]
    assert isinstance(result, ChannelFrame)
    return result


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


def _channel(
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    context: FrameContext,
) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(data),
        ChannelSemantic.HUE,
        -2.0,
        3.0,
        True,
        context,
    )


@pytest.mark.parametrize("type_id", BATCH4_IDS)
def test_batch4_definitions_propagate_no_data(type_id: str) -> None:
    assert_scheduler_propagates_no_data(_definition(type_id), {})


def test_batch4_metadata_has_exact_order_ports_defaults_and_choices() -> None:
    definitions = [item for item in create_image_definitions() if item.type_id in BATCH4_IDS]
    assert tuple(item.type_id for item in definitions) == BATCH4_IDS

    for definition in definitions:
        assert definition.execution_kind is ExecutionKind.STATELESS
        assert (
            tuple(parameter.id for parameter in definition.parameters)
            == PARAMETER_IDS[definition.type_id]
        )
        assert (
            tuple(parameter.default for parameter in definition.parameters)
            == DEFAULTS[definition.type_id]
        )
        assert {
            parameter.id for parameter in definition.parameters if parameter.connectable
        } == CONNECTABLE_IDS[definition.type_id]

    threshold = _definition("synmachine.image.threshold")
    assert threshold.category == "Image / Analysis"
    assert tuple((port.id, port.value_type) for port in threshold.inputs) == (
        ("channel", PortType.CHANNEL),
    )
    assert tuple((port.id, port.value_type) for port in threshold.outputs) == (
        ("channel", PortType.CHANNEL),
    )
    assert threshold.parameter("mode").choices == tuple(CV_THRESHOLDS)  # type: ignore[union-attr]
    for parameter_id in ("threshold", "maximum"):
        parameter = threshold.parameter(parameter_id)
        assert parameter is not None and parameter.connectable
        assert parameter.connected_port_type is PortType.FLOAT

    canny = _definition("synmachine.image.canny")
    assert canny.category == "Image / Analysis"
    assert canny.inputs[0].value_type is PortType.IMAGE
    assert canny.outputs[0].value_type is PortType.CHANNEL
    assert canny.parameter("aperture_size").choices == (3, 5, 7)  # type: ignore[union-attr]

    convolve = _definition("synmachine.image.convolve")
    assert convolve.parameter("kernel").value_type is PortType.MATRIX  # type: ignore[union-attr]
    assert convolve.parameter("normalization").choices == (  # type: ignore[union-attr]
        "NONE",
        "SUM_TO_ONE",
        "ABSOLUTE_SUM_TO_ONE",
    )
    for type_id in IMAGE_OUTPUT_IDS:
        definition = _definition(type_id)
        assert definition.category == "Image / Filter"
        assert tuple(port.id for port in definition.inputs) == ("image",)
        assert tuple(port.id for port in definition.outputs) == ("image",)
        assert {
            parameter.id for parameter in definition.parameters if parameter.connectable
        } == CONNECTABLE_IDS[type_id]


def test_threshold_default_conforms_and_preserves_channel_metadata(
    image_context: FrameContext,
) -> None:
    source = _channel(
        np.array([[0.1, 0.5, 0.6], [np.nan, np.inf, -np.inf]], np.float32), image_context
    )
    before = source.data.copy()
    result = _process_channel(source)
    assert_channel_conformance(source, result, before)


@pytest.mark.parametrize(("mode", "native_mode"), CV_THRESHOLDS.items())
def test_threshold_matches_all_native_modes_including_non_finite_values(
    mode: str,
    native_mode: int,
    image_context: FrameContext,
) -> None:
    source = _channel(
        np.array([[-np.inf, -0.2, 0.5, 0.75, np.inf, np.nan]], dtype=np.float32),
        image_context,
    )
    result = _process_channel(source, {"mode": mode, "threshold": 0.5, "maximum": 2.0})
    _, expected = cv2.threshold(source.data, 0.5, 2.0, native_mode)
    assert np.array_equal(result.data, expected, equal_nan=True)


def test_threshold_connected_scalars_override_literal_fallback(image_context: FrameContext) -> None:
    source = _channel(np.array([[0.2, 0.6, 0.9]], dtype=np.float32), image_context)
    result = _process_channel(
        source,
        {"threshold": 0.95, "maximum": -1.0},
        connected={"threshold": 0.5, "maximum": 3.0},
    )
    assert np.array_equal(result.data, np.array([[0.0, 3.0, 3.0]], dtype=np.float32))


def test_canny_matches_descriptor_luminance_uint8_reference_and_conforms(
    rgb_image: ImageFrame,
) -> None:
    data = np.zeros((9, 11, 3), dtype=np.float32)
    data[2:7, 3:8] = (1.0, 0.4, 0.1)
    source = _image(data, ColorSpace.SRGB, rgb_image)
    before = source.data.copy()
    result = _process_canny(source, {"low_threshold": 0.05, "high_threshold": 0.2})
    luminance = image_to_luminance(source).data
    native_source = np.rint(np.clip(luminance, 0.0, 1.0) * np.float32(255.0)).astype(np.uint8)
    expected = cv2.Canny(native_source, 0.05 * 255.0, 0.2 * 255.0) / np.float32(255.0)

    assert np.array_equal(result.data, expected)
    assert result.data.dtype == np.float32
    assert result.data.flags.c_contiguous and not result.data.flags.writeable
    assert result.context is source.context
    assert result.semantic is ChannelSemantic.LUMINANCE
    assert (result.nominal_min, result.nominal_max, result.cyclic) == (0.0, 1.0, False)
    assert set(np.unique(result.data).tolist()) <= {0.0, 1.0}
    assert np.array_equal(source.data, before)


def test_canny_honours_preblur_aperture_and_l2_gradient(rgb_image: ImageFrame) -> None:
    data = np.zeros((21, 23, 3), dtype=np.float32)
    data[5:16, 7:18] = 1.0
    source = _image(data, ColorSpace.LINEAR_RGB, rgb_image)
    result = _process_canny(
        source,
        {
            "low_threshold": 0.03,
            "high_threshold": 0.25,
            "aperture_size": 5,
            "l2_gradient": True,
            "pre_blur_sigma": 1.25,
        },
    )
    luminance = image_to_luminance(source).data
    blurred = cv2.GaussianBlur(
        luminance,
        (0, 0),
        sigmaX=1.25,
        sigmaY=1.25,
        borderType=cv2.BORDER_REFLECT_101,
    )
    native_source = np.rint(np.clip(blurred, 0.0, 1.0) * np.float32(255.0)).astype(np.uint8)
    expected = cv2.Canny(
        native_source,
        0.03 * 255.0,
        0.25 * 255.0,
        apertureSize=5,
        L2gradient=True,
    )
    assert np.array_equal(result.data, expected.astype(np.float32) / np.float32(255.0))


def test_canny_ignores_alpha_including_non_finite_alpha(rgba_image: ImageFrame) -> None:
    data = np.zeros((12, 14, 4), dtype=np.float32)
    data[3:10, 4:11, :3] = (0.9, 0.2, 0.6)
    data[..., 3] = np.nan
    first = _image(data, ColorSpace.RGBA, rgba_image)
    altered = np.array(data, copy=True)
    altered[..., 3] = np.inf
    second = _image(altered, ColorSpace.RGBA, rgba_image)
    assert np.array_equal(_process_canny(first).data, _process_canny(second).data)


def test_canny_rejects_non_finite_non_alpha_values(rgba_image: ImageFrame) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 0, 0] = np.nan
    source = _image(data, ColorSpace.RGBA, rgba_image)
    with pytest.raises(ExpectedNodeError, match="finite") as captured:
        _process_canny(source)
    assert captured.value.code == "invalid_canny"


@pytest.mark.parametrize("type_id", IMAGE_OUTPUT_IDS)
def test_batch4_image_defaults_conform_without_mutating_rgba(
    type_id: str,
    rgba_image: ImageFrame,
) -> None:
    before = rgba_image.data.copy()
    result = _process_image(type_id, rgba_image)
    assert_image_conformance(rgba_image, result, before, expected_shape=rgba_image.data.shape)


def test_convolve_identity_and_alpha_preservation(rgba_image: ImageFrame) -> None:
    result = _process_image("synmachine.image.convolve", rgba_image)
    assert np.array_equal(result.data, rgba_image.data)


@pytest.mark.parametrize(
    ("normalization", "rows"),
    [
        ("NONE", ((0.0, 1.0, 0.0), (1.0, -4.0, 1.0), (0.0, 1.0, 0.0))),
        ("SUM_TO_ONE", ((1.0, 2.0, 1.0), (2.0, 4.0, 2.0), (1.0, 2.0, 1.0))),
        (
            "ABSOLUTE_SUM_TO_ONE",
            ((-1.0, -2.0, -1.0), (0.0, 0.0, 0.0), (1.0, 2.0, 1.0)),
        ),
    ],
)
def test_convolve_matches_native_normalization_scale_delta_and_non_finite_semantics(
    normalization: str,
    rows: tuple[tuple[float, ...], ...],
    rgb_image: ImageFrame,
) -> None:
    data = np.array(rgb_image.data, copy=True)
    data[1, 2, 0] = np.nan
    source = _image(data, ColorSpace.SRGB, rgb_image)
    kernel = np.asarray(rows, dtype=np.float32)
    if normalization == "SUM_TO_ONE":
        kernel = kernel / np.float32(np.sum(kernel, dtype=np.float64))
    elif normalization == "ABSOLUTE_SUM_TO_ONE":
        kernel = kernel / np.float32(np.sum(np.abs(kernel), dtype=np.float64))
    expected = cv2.filter2D(
        source.data,
        -1,
        kernel * np.float32(1.5),
        delta=-0.2,
        borderType=cv2.BORDER_REPLICATE,
    )
    result = _process_image(
        "synmachine.image.convolve",
        source,
        {
            "kernel": NumericMatrix(rows),
            "normalization": normalization,
            "scale": 1.5,
            "delta": -0.2,
            "border_mode": "REPLICATE",
        },
    )
    assert np.array_equal(result.data, expected, equal_nan=True)


def test_convolve_wrap_matches_exact_wrapped_halo(rgba_image: ImageFrame) -> None:
    rows = ((1.0, 0.0, -1.0), (2.0, 0.0, -2.0), (1.0, 0.0, -1.0))
    result = _process_image(
        "synmachine.image.convolve",
        rgba_image,
        {"kernel": NumericMatrix(rows), "border_mode": "WRAP"},
    )
    padded = np.pad(rgba_image.data[..., :3], ((1, 1), (1, 1), (0, 0)), mode="wrap")
    filtered = cv2.filter2D(
        padded,
        -1,
        np.asarray(rows, dtype=np.float32),
        borderType=cv2.BORDER_CONSTANT,
    )[1:-1, 1:-1]
    assert np.array_equal(result.data[..., :3], filtered)
    assert np.array_equal(result.data[..., 3], rgba_image.data[..., 3])


@pytest.mark.parametrize(
    "overrides",
    [
        {"kernel": NumericMatrix(((1e39,),))},
        {"kernel": NumericMatrix(((1.0,),)), "scale": 1e39},
    ],
)
def test_convolve_rejects_effective_kernel_coefficients_that_overflow_float32(
    overrides: Mapping[str, object],
    rgb_image: ImageFrame,
) -> None:
    with pytest.raises(ExpectedNodeError, match="finite float32") as captured:
        _process_image("synmachine.image.convolve", rgb_image, overrides)
    assert captured.value.code == "invalid_convolve"


@pytest.mark.parametrize("type_id", ("synmachine.image.dilate", "synmachine.image.erode"))
@pytest.mark.parametrize("kernel_shape", tuple(CV_MORPH_SHAPES))
def test_morphology_shapes_match_native_and_preserve_alpha(
    type_id: str,
    kernel_shape: str,
    rgba_image: ImageFrame,
) -> None:
    result = _process_image(type_id, rgba_image, {"kernel_shape": kernel_shape})
    kernel = cv2.getStructuringElement(CV_MORPH_SHAPES[kernel_shape], (3, 3), (-1, -1))
    operation = cv2.dilate if type_id.endswith("dilate") else cv2.erode
    expected = operation(
        rgba_image.data[..., :3],
        kernel,
        anchor=(-1, -1),
        iterations=1,
        borderType=cv2.BORDER_REFLECT_101,
    )
    assert np.array_equal(result.data[..., :3], expected)
    assert np.array_equal(result.data[..., 3], rgba_image.data[..., 3])


@pytest.mark.parametrize("type_id", ("synmachine.image.dilate", "synmachine.image.erode"))
def test_morphology_custom_cross_anchor_iterations_and_alpha_processing_match_native(
    type_id: str,
    rgba_image: ImageFrame,
) -> None:
    data = np.arange(5 * 7 * 4, dtype=np.float32).reshape(5, 7, 4) / np.float32(71.0)
    source = _image(data, ColorSpace.RGBA, rgba_image)
    overrides: dict[str, object] = {
        "kernel_shape": "CROSS",
        "kernel_width": 5,
        "kernel_height": 3,
        "iterations": 2,
        "anchor_x": 1,
        "anchor_y": 0,
        "border_mode": "REPLICATE",
        "process_alpha": True,
    }
    result = _process_image(type_id, source, overrides)
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (5, 3), (1, 0))
    operation = cv2.dilate if type_id.endswith("dilate") else cv2.erode
    expected = operation(
        source.data,
        kernel,
        anchor=(1, 0),
        iterations=2,
        borderType=cv2.BORDER_REPLICATE,
    )
    assert np.array_equal(result.data, expected)
    assert not np.array_equal(result.data[..., 3], source.data[..., 3])


@pytest.mark.parametrize("type_id", ("synmachine.image.dilate", "synmachine.image.erode"))
def test_morphology_wrap_handles_asymmetric_anchor_and_multiple_iterations(
    type_id: str,
    rgb_image: ImageFrame,
) -> None:
    data = np.arange(6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3) / np.float32(143.0)
    source = _image(data, ColorSpace.SRGB, rgb_image)
    overrides: dict[str, object] = {
        "kernel_shape": "CROSS",
        "iterations": 2,
        "anchor_x": 0,
        "anchor_y": 2,
        "border_mode": "WRAP",
    }
    result = _process_image(type_id, source, overrides)
    padded = np.pad(source.data, ((4, 0), (0, 4), (0, 0)), mode="wrap")
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3), (0, 2))
    operation = cv2.dilate if type_id.endswith("dilate") else cv2.erode
    filtered = operation(
        padded,
        kernel,
        anchor=(0, 2),
        iterations=2,
        borderType=cv2.BORDER_CONSTANT,
    )
    expected = filtered[4 : 4 + source.data.shape[0], : source.data.shape[1]]
    assert np.array_equal(result.data, expected)


@pytest.mark.parametrize("type_id", ("synmachine.image.dilate", "synmachine.image.erode"))
def test_morphology_keeps_native_non_finite_semantics(
    type_id: str,
    rgb_image: ImageFrame,
) -> None:
    data = np.array(rgb_image.data, copy=True)
    data[1, 2, 0] = np.nan
    data[2, 3, 1] = np.inf
    source = _image(data, ColorSpace.SRGB, rgb_image)
    result = _process_image(type_id, source, {"border_mode": "CONSTANT"})
    operation = cv2.dilate if type_id.endswith("dilate") else cv2.erode
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3), (-1, -1))
    expected = operation(
        source.data,
        kernel,
        anchor=(-1, -1),
        iterations=1,
        borderType=cv2.BORDER_CONSTANT,
    )
    assert np.array_equal(result.data, expected, equal_nan=True)


def test_high_and_low_pass_match_automatic_gaussian_formula_without_clipping(
    rgba_image: ImageFrame,
) -> None:
    data = np.array(rgba_image.data, copy=True)
    data[0, 1, :3] = 2.5
    source = _image(data, ColorSpace.RGBA, rgba_image)
    blur = cv2.GaussianBlur(
        source.data[..., :3],
        (0, 0),
        sigmaX=0.8,
        sigmaY=0.8,
        borderType=cv2.BORDER_REPLICATE,
    )
    low = _process_image(
        "synmachine.image.low_pass",
        source,
        {"sigma": 0.8, "border_mode": "REPLICATE"},
    )
    high = _process_image(
        "synmachine.image.high_pass",
        source,
        {
            "sigma": 0.8,
            "display_offset": 0.5,
            "gain": 2.0,
            "border_mode": "REPLICATE",
        },
    )
    assert np.array_equal(low.data[..., :3], blur)
    assert np.array_equal(
        high.data[..., :3],
        np.float32(0.5) + np.float32(2.0) * (source.data[..., :3] - blur),
    )
    assert np.array_equal(low.data[..., 3], source.data[..., 3])
    assert np.array_equal(high.data[..., 3], source.data[..., 3])
    assert np.any(high.data[..., :3] < 0.0) or np.any(high.data[..., :3] > 1.0)


@pytest.mark.parametrize("type_id", ("synmachine.image.high_pass", "synmachine.image.low_pass"))
def test_pass_filters_wrap_and_non_finite_values_match_wrapped_gaussian(
    type_id: str,
    rgb_image: ImageFrame,
) -> None:
    data = np.array(rgb_image.data, copy=True)
    data[1, 2, 0] = np.nan
    source = _image(data, ColorSpace.SRGB, rgb_image)
    result = _process_image(type_id, source, {"sigma": 1.0, "border_mode": "WRAP"})
    padded = np.pad(source.data, ((4, 4), (4, 4), (0, 0)), mode="wrap")
    blur = cv2.GaussianBlur(
        padded,
        (0, 0),
        sigmaX=1.0,
        sigmaY=1.0,
        borderType=cv2.BORDER_CONSTANT,
    )[4:-4, 4:-4]
    expected = source.data - blur if type_id.endswith("high_pass") else blur
    assert np.array_equal(result.data, expected, equal_nan=True)


@pytest.mark.parametrize(
    ("type_id", "overrides", "message"),
    [
        ("synmachine.image.threshold", {"threshold": float("nan")}, "threshold must be finite"),
        ("synmachine.image.canny", {"low_threshold": 0.8, "high_threshold": 0.2}, "greater"),
        ("synmachine.image.canny", {"aperture_size": 4}, "one of"),
        (
            "synmachine.image.convolve",
            {"kernel": NumericMatrix(((1.0, 1.0), (1.0, 1.0)))},
            "positive odd",
        ),
        (
            "synmachine.image.convolve",
            {"kernel": NumericMatrix((tuple(1.0 for _ in range(17)),))},
            "not exceed 15",
        ),
        (
            "synmachine.image.convolve",
            {
                "kernel": NumericMatrix(((1.0, -1.0, 0.0),)),
                "normalization": "SUM_TO_ONE",
            },
            "denominator",
        ),
        ("synmachine.image.dilate", {"kernel_width": 2}, "increments of 2"),
        ("synmachine.image.erode", {"anchor_x": 3, "anchor_y": 0}, "anchor"),
        ("synmachine.image.high_pass", {"sigma": 0.0}, "at least"),
        ("synmachine.image.low_pass", {"sigma": float("inf")}, "finite"),
    ],
)
def test_definition_validators_reject_invalid_batch4_literals(
    type_id: str,
    overrides: Mapping[str, object],
    message: str,
) -> None:
    _, errors = _definition(type_id).parameter_values(overrides)
    assert any(message in error for error in errors)


@pytest.mark.parametrize(
    ("type_id", "mutations", "message"),
    [
        ("synmachine.image.threshold", {"mode": "NOT_A_MODE"}, "not a valid"),
        ("synmachine.image.canny", {"high_threshold": 0.05}, "greater"),
        ("synmachine.image.convolve", {"kernel": "not-a-matrix"}, "NumericMatrix"),
        ("synmachine.image.dilate", {"iterations": 0}, "positive"),
        ("synmachine.image.erode", {"anchor_x": -1, "anchor_y": 0}, "anchor"),
        ("synmachine.image.high_pass", {"sigma": 0.0}, "positive"),
        ("synmachine.image.low_pass", {"border_mode": "NOT_A_BORDER"}, "not a valid"),
    ],
)
def test_malformed_runtime_parameters_produce_recoverable_errors(
    type_id: str,
    mutations: Mapping[str, ParameterValue],
    message: str,
    rgb_image: ImageFrame,
    image_context: FrameContext,
) -> None:
    definition = _definition(type_id)
    parameters = _parameters(definition)
    parameters.update(mutations)
    inputs: dict[str, RuntimeValue]
    if type_id.endswith("threshold"):
        inputs = {"channel": _channel(np.zeros((2, 3), dtype=np.float32), image_context)}
    else:
        inputs = {"image": rgb_image}
    with pytest.raises(ExpectedNodeError, match=message):
        _runtime_process(definition, inputs, parameters, rgb_image.context)
