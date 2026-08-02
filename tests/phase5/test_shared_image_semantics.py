"""Tests for shared border, kernel, finite, and colour semantics."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from synesthesia_machine.contracts import ColorSpace, ColorValue
from synesthesia_machine.media import (
    BorderMode,
    color_value_for_space,
    finite_report,
    sanitize_finite,
    validate_odd_kernel,
)
from synesthesia_machine.media.image_common import (
    cv_border_mode,
    recombine_alpha,
    split_alpha,
)


def test_common_border_mapping_and_odd_kernel_validation() -> None:
    assert cv_border_mode(BorderMode.REFLECT_101) == cv2.BORDER_REFLECT_101
    assert cv_border_mode(BorderMode.WRAP) == cv2.BORDER_WRAP
    validate_odd_kernel(3, 5, maximum=15)
    for dimensions in ((0, 3), (2, 3), (3, 16)):
        with pytest.raises(ValueError):
            validate_odd_kernel(*dimensions, maximum=15)


def test_finite_report_and_sanitation_are_vectorized_and_float32() -> None:
    data = np.array([[np.nan, np.inf, -np.inf, 0.25]], dtype=np.float32)
    report = finite_report(data)
    safe, sanitation = sanitize_finite(data)
    assert report == sanitation
    assert report.non_finite_count == 3
    assert safe.dtype == np.float32
    assert safe.tolist() == [[0.0, 1.0, 0.0, 0.25]]


def test_colour_value_conversion_follows_target_descriptor() -> None:
    value = ColorValue(1.0, 0.0, 0.0, 0.25)
    assert np.allclose(color_value_for_space(value, ColorSpace.LINEAR_RGB), (1.0, 0.0, 0.0))
    rgba = color_value_for_space(value, ColorSpace.RGBA)
    assert len(rgba) == 4 and rgba[3] == pytest.approx(0.25)
    hsv = color_value_for_space(value, ColorSpace.HSV)
    assert np.allclose(hsv, (0.0, 1.0, 1.0), atol=1e-6)


def test_alpha_helpers_follow_descriptor_order_without_mutating_input() -> None:
    data = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
    before = data.copy()
    colour, alpha = split_alpha(data, ColorSpace.RGBA)
    assert alpha is not None
    assert np.shares_memory(colour, data)
    assert np.shares_memory(alpha, data)
    assert np.array_equal(colour, data[..., :3])
    assert np.array_equal(alpha, data[..., 3])
    assert np.array_equal(recombine_alpha(colour, alpha, ColorSpace.RGBA), data)
    assert np.array_equal(data, before)
