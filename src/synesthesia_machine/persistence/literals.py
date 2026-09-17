"""Single JSON literal codec shared by graph files and clipboard fragments.

``LiteralValue`` payloads (node parameters, document settings, connection and
node UI state) round-trip through JSON as plain literals. Two kinds need
structured shapes: ``ColorValue`` encodes as ``{"$type": "COLOR", "r", "g",
"b", "a"}`` and ``NumericMatrix`` as nested arrays. The graph file reader
(``graph_io``) and the clipboard fragment reader (``clipboard``) both decode
and encode through this module, so adding a new structured literal kind is
one edit instead of two divergent copies.

Decode failures raise :class:`LiteralDecodeError` with a machine-readable
``kind``. Each consumer re-raises it in its own error vocabulary:
``GraphPersistenceError`` for graph files, plain ``ValueError`` for
clipboard text.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from synesthesia_machine.contracts import ColorValue, JsonValue, NumericMatrix
from synesthesia_machine.graph import LiteralValue

_COLOR_KEYS = {"$type", "r", "g", "b", "a"}


@dataclass(frozen=True, slots=True)
class LiteralDecodeError(ValueError):
    """A structured-literal decode failure before consumer re-translation."""

    kind: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        location = f" at {self.path}" if self.path is not None else ""
        return f"{self.kind}{location}: {self.message}"


def number(value: object, path: str) -> float:
    """Validate a finite JSON number and widen it to float."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiteralDecodeError("invalid_type", "Expected number", path)
    try:
        result = float(value)
    except OverflowError as error:
        raise LiteralDecodeError("invalid_number", "Expected finite number", path) from error
    if not math.isfinite(result):
        raise LiteralDecodeError("invalid_number", "Expected finite number", path)
    return result


def literal_value(value: object, path: str) -> LiteralValue:
    """Decode one JSON value into a :class:`LiteralValue`."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return numeric_matrix(cast(object, value), path)
    if isinstance(value, dict):
        data = _object(cast(object, value), path)
        if set(data) != _COLOR_KEYS:
            raise LiteralDecodeError("invalid_literal", "Expected a finite JSON literal", path)
        raw_type = data["$type"]
        if not isinstance(raw_type, str) or raw_type != "COLOR":
            raise LiteralDecodeError(
                "invalid_literal", "Unknown structured literal type", f"{path}.$type"
            )
        try:
            return ColorValue(
                number(data["r"], f"{path}.r"),
                number(data["g"], f"{path}.g"),
                number(data["b"], f"{path}.b"),
                number(data["a"], f"{path}.a"),
            )
        except ValueError as error:
            raise LiteralDecodeError("invalid_literal", str(error), path) from error
    raise LiteralDecodeError("invalid_literal", "Expected a finite JSON literal", path)


def literal_mapping(value: object, path: str) -> dict[str, LiteralValue]:
    """Decode a JSON object into a ``LiteralValue`` mapping keyed by name."""

    data = _object(value, path)
    return {key: literal_value(item, f"{path}.{key}") for key, item in data.items()}


def literal_to_data(value: LiteralValue) -> JsonValue:
    """Encode one :class:`LiteralValue` as JSON-compatible data. Cannot fail."""

    if isinstance(value, ColorValue):
        return {"$type": "COLOR", "r": value.r, "g": value.g, "b": value.b, "a": value.a}
    if isinstance(value, NumericMatrix):
        return [list(row) for row in value.rows]
    return value


def literal_mapping_to_data(values: Mapping[str, LiteralValue]) -> dict[str, JsonValue]:
    """Encode a ``LiteralValue`` mapping deterministically, sorted by key."""

    return {key: literal_to_data(value) for key, value in sorted(values.items())}


def numeric_matrix(value: object, path: str) -> NumericMatrix:
    """Decode a JSON array of arrays into a :class:`NumericMatrix`."""

    rows = _array(value, path)
    parsed_rows: list[tuple[float, ...]] = []
    for row_index, row in enumerate(rows):
        raw_row = _array(row, f"{path}[{row_index}]")
        parsed_rows.append(
            tuple(
                number(item, f"{path}[{row_index}][{column_index}]")
                for column_index, item in enumerate(raw_row)
            )
        )
    try:
        return NumericMatrix(tuple(parsed_rows))
    except (TypeError, ValueError) as error:
        raise LiteralDecodeError("invalid_literal", str(error), path) from error


def _object(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LiteralDecodeError("invalid_type", "Expected JSON object", path)
    raw = cast(dict[object, object], value)
    for key in raw:
        if not isinstance(key, str):
            raise LiteralDecodeError("invalid_type", "Expected JSON object", path)
    return cast(dict[str, object], raw)


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise LiteralDecodeError("invalid_type", "Expected JSON array", path)
    return cast(list[object], value)


__all__ = [
    "LiteralDecodeError",
    "literal_mapping",
    "literal_mapping_to_data",
    "literal_to_data",
    "literal_value",
    "number",
    "numeric_matrix",
]
