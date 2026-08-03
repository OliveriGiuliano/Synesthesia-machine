"""Small runtime-boundary helpers shared by image node families."""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.contracts import ChannelFrame, ColorValue, ImageFrame, NumericMatrix
from synesthesia_machine.nodes import ResetReason


class StatelessImageRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def image_value(value: object) -> ImageFrame:
    if isinstance(value, ImageFrame):
        return value
    raise TypeError(f"Expected ImageFrame, got {type(value).__name__}")


def channel_value(value: object) -> ChannelFrame:
    if isinstance(value, ChannelFrame):
        return value
    raise TypeError(f"Expected ChannelFrame, got {type(value).__name__}")


def integer_value(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected integer, got {type(value).__name__}")


def number_value(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected number, got {type(value).__name__}")


def boolean_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean, got {type(value).__name__}")


def text_value(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string, got {type(value).__name__}")


def color_value(value: object) -> ColorValue:
    if isinstance(value, ColorValue):
        return value
    raise TypeError(f"Expected ColorValue, got {type(value).__name__}")


def matrix_value(value: object) -> NumericMatrix:
    if isinstance(value, NumericMatrix):
        return value
    raise TypeError(f"Expected NumericMatrix, got {type(value).__name__}")


__all__ = [
    "StatelessImageRuntime",
    "boolean_value",
    "channel_value",
    "color_value",
    "image_value",
    "integer_value",
    "matrix_value",
    "number_value",
    "text_value",
]
