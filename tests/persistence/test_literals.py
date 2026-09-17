"""Shared JSON literal codec: round-trips, rejections, and error diagnostics."""

from __future__ import annotations

import math

import pytest

from synesthesia_machine.contracts import ColorValue, NumericMatrix
from synesthesia_machine.persistence.literals import (
    LiteralDecodeError,
    literal_mapping,
    literal_mapping_to_data,
    literal_to_data,
    literal_value,
    number,
)


def test_scalar_literals_pass_through_unchanged() -> None:
    assert literal_value(None, "$.a") is None
    assert literal_value("text", "$.a") == "text"
    assert literal_value(True, "$.a") is True
    assert literal_value(7, "$.a") == 7
    assert literal_value(1.5, "$.a") == 1.5


def test_color_literal_round_trips_through_its_json_shape() -> None:
    color = ColorValue(0.1, 0.2, 0.3, 0.4)
    data = literal_to_data(color)
    assert data == {"$type": "COLOR", "r": 0.1, "g": 0.2, "b": 0.3, "a": 0.4}
    assert literal_value(data, "$.accent") == color


def test_numeric_matrix_round_trips_as_nested_arrays() -> None:
    matrix = NumericMatrix(((1.0, 2.0), (3.0, 4.0)))
    data = literal_to_data(matrix)
    assert data == [[1.0, 2.0], [3.0, 4.0]]
    assert literal_value(data, "$.kernel") == matrix


def test_literal_mapping_round_trips_sorted_by_key() -> None:
    row = (1.0, 2.0)
    values = {
        "b": ColorValue(0.0, 0.0, 0.0),
        "a": NumericMatrix((row,)),
        "z": 3,
    }
    data = literal_mapping_to_data(values)
    assert list(data) == ["a", "b", "z"]
    assert literal_mapping(data, "$") == values


@pytest.mark.parametrize(
    ("value", "kind", "path"),
    [
        (float("nan"), "invalid_literal", "$.a"),
        (float("inf"), "invalid_literal", "$.a"),
        ([True], "invalid_type", "$.a[0]"),
        ([[10**1000]], "invalid_number", "$.a[0][0]"),
        ([[1.0], [2.0, 3.0]], "invalid_literal", "$.a"),
        ([], "invalid_literal", "$.a"),
        ([1.0, 2.0], "invalid_type", "$.a[0]"),
    ],
)
def test_codec_reports_failure_kind_and_path(value: object, kind: str, path: str) -> None:
    with pytest.raises(LiteralDecodeError) as captured:
        literal_value(value, "$.a")
    assert captured.value.kind == kind
    assert captured.value.path is not None
    assert path in captured.value.path


def test_color_literal_rejects_unknown_type_tag() -> None:
    with pytest.raises(LiteralDecodeError) as captured:
        literal_value({"$type": "GRADIENT", "r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}, "$.accent")
    assert captured.value.kind == "invalid_literal"
    assert captured.value.path == "$.accent.$type"


def test_color_literal_rejects_missing_components() -> None:
    with pytest.raises(LiteralDecodeError) as captured:
        literal_value({"$type": "COLOR", "r": 0.0}, "$.accent")
    assert captured.value.kind == "invalid_literal"
    assert captured.value.path == "$.accent"


def test_number_rejects_bools_and_non_finite_values() -> None:
    with pytest.raises(LiteralDecodeError) as bool_error:
        number(True, "$.n")
    assert bool_error.value.kind == "invalid_type"
    with pytest.raises(LiteralDecodeError) as overflow_error:
        number(10**1000, "$.n")
    assert overflow_error.value.kind == "invalid_number"
    assert math.isfinite(number(3, "$.n"))


def test_literal_mapping_rejects_non_object_payloads() -> None:
    with pytest.raises(LiteralDecodeError) as captured:
        literal_mapping([1.0, 2.0], "$.parameters")
    assert captured.value.kind == "invalid_type"
    assert captured.value.path == "$.parameters"
