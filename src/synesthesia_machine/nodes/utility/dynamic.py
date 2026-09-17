"""Shared machinery for the polymorphic utility node families.

The temporal, analysis, and transform modules each own their runtimes and
definitions; this module declares what they share: the dynamic type-variable
constraints (a value may be a float, int, image, or channel frame) and the
value-frame reconstruction and descriptor-compatibility helpers.
"""

import math
from collections.abc import Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    ImageFrame,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.media.image_common import frame_like
from synesthesia_machine.nodes import ArrayTypeVariable, ExpectedNodeError, TypeVariable

_DYNAMIC_TYPES = frozenset({PortType.FLOAT, PortType.INT, PortType.IMAGE, PortType.CHANNEL})
T = TypeVariable("T", _DYNAMIC_TYPES)
T_ARRAY = ArrayTypeVariable("T", _DYNAMIC_TYPES)


def value_like(value: ImageFrame | ChannelFrame, data: NDArray[np.floating]) -> RuntimeValue:
    converted = np.asarray(data, dtype=np.float32)
    return (
        frame_like(value, converted)
        if isinstance(value, ImageFrame)
        else channel_like(value, converted)
    )


def channel_like(channel: ChannelFrame, data: NDArray[np.floating]) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(data, dtype=np.float32)),
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )


def same_descriptor(first: ImageFrame | ChannelFrame, second: ImageFrame | ChannelFrame) -> bool:
    if type(first) is not type(second) or first.data.shape != second.data.shape:
        return False
    if first.context.clock_id != second.context.clock_id:
        return False
    if isinstance(first, ImageFrame):
        assert isinstance(second, ImageFrame)
        return (
            first.color_space == second.color_space
            and first.channel_names == second.channel_names
            and first.alpha_mode == second.alpha_mode
        )
    assert isinstance(first, ChannelFrame) and isinstance(second, ChannelFrame)
    return (
        first.semantic == second.semantic
        and first.nominal_min == second.nominal_min
        and first.nominal_max == second.nominal_max
        and first.cyclic == second.cyclic
    )


def require_matching_descriptors(values: Sequence[ImageFrame | ChannelFrame]) -> None:
    if any(value.data.shape != values[0].data.shape for value in values[1:]):
        raise ExpectedNodeError(
            "statistics_shape", "Statistics array values must have equal shapes"
        )
    if any(not same_descriptor(values[0], value) for value in values[1:]):
        raise ExpectedNodeError(
            "statistics_descriptor",
            "Statistics array values must use matching descriptors and source clocks",
        )


def positive_number(value: object, name: str) -> float:
    result = cast(float, value)
    if not math.isfinite(result) or result <= 0.0:
        raise ExpectedNodeError("invalid_parameter", f"{name} must be finite and positive")
    return result


__all__ = [
    "T_ARRAY",
    "T",
    "channel_like",
    "positive_number",
    "require_matching_descriptors",
    "same_descriptor",
    "value_like",
]
