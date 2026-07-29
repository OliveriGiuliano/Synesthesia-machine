"""Immutable runtime values shared by the graph compiler, scheduler, and nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final
from uuid import UUID

import numpy as np
from numpy.typing import NDArray


class PortType(StrEnum):
    IMAGE = "IMAGE"
    CHANNEL = "CHANNEL"
    FLOAT = "FLOAT"
    INT = "INT"
    BOOL = "BOOL"
    COLOR = "COLOR"
    MIDI_STATE = "MIDI_STATE"
    STRING = "STRING"


class ColorSpace(StrEnum):
    LINEAR_RGB = "LINEAR_RGB"
    SRGB = "SRGB"
    HSV = "HSV"
    LAB = "LAB"


class AlphaMode(StrEnum):
    NONE = "NONE"
    STRAIGHT = "STRAIGHT"
    PREMULTIPLIED = "PREMULTIPLIED"


class ChannelSemantic(StrEnum):
    GENERIC = "GENERIC"
    LUMINANCE = "LUMINANCE"
    RED = "RED"
    GREEN = "GREEN"
    BLUE = "BLUE"
    ALPHA = "ALPHA"
    HUE = "HUE"
    SATURATION = "SATURATION"
    VALUE = "VALUE"


@dataclass(frozen=True, slots=True)
class FrameContext:
    clock_id: UUID
    tick_index: int
    source_frame_index: int | None
    source_time_s: float
    received_monotonic_ns: int
    deadline_monotonic_ns: int | None
    is_realtime: bool

    def __post_init__(self) -> None:
        if self.tick_index < 1:
            msg = "tick_index must be at least 1"
            raise ValueError(msg)
        if self.source_frame_index is not None and self.source_frame_index < 0:
            msg = "source_frame_index cannot be negative"
            raise ValueError(msg)
        if self.source_time_s < 0.0:
            msg = "source_time_s cannot be negative"
            raise ValueError(msg)
        if self.received_monotonic_ns < 0:
            msg = "received_monotonic_ns cannot be negative"
            raise ValueError(msg)
        if self.deadline_monotonic_ns is not None and self.deadline_monotonic_ns < 0:
            msg = "deadline_monotonic_ns cannot be negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class FrameProvenance:
    source_node_id: UUID
    source_kind: str

    def __post_init__(self) -> None:
        if not self.source_kind:
            msg = "source_kind cannot be empty"
            raise ValueError(msg)


def _validate_read_only_float32(data: NDArray[np.float32], *, dimensions: int) -> None:
    if data.dtype != np.float32:
        msg = "runtime arrays must use float32"
        raise TypeError(msg)
    if data.ndim != dimensions:
        msg = f"runtime array must have {dimensions} dimensions"
        raise ValueError(msg)
    if data.flags.writeable:
        msg = "runtime arrays must be read-only"
        raise ValueError(msg)


def read_only_float32(data: NDArray[np.float32]) -> NDArray[np.float32]:
    """Return a C-contiguous float32 copy marked read-only for a runtime boundary."""

    result = np.array(data, dtype=np.float32, order="C", copy=True)
    result.flags.writeable = False
    return result


@dataclass(frozen=True, slots=True)
class ImageFrame:
    data: NDArray[np.float32]
    color_space: ColorSpace
    channel_names: tuple[str, ...]
    alpha_mode: AlphaMode
    context: FrameContext
    provenance: FrameProvenance

    def __post_init__(self) -> None:
        _validate_read_only_float32(self.data, dimensions=3)
        if self.data.shape[2] < 1:
            msg = "image data must contain at least one channel"
            raise ValueError(msg)
        if self.data.shape[2] != len(self.channel_names):
            msg = "channel_names must match the image channel count"
            raise ValueError(msg)
        if not self.data.flags.c_contiguous:
            msg = "image data must be C-contiguous"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ChannelFrame:
    data: NDArray[np.float32]
    semantic: ChannelSemantic
    nominal_min: float
    nominal_max: float
    cyclic: bool
    context: FrameContext

    def __post_init__(self) -> None:
        _validate_read_only_float32(self.data, dimensions=2)
        if self.nominal_max <= self.nominal_min:
            msg = "nominal_max must be greater than nominal_min"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ColorValue:
    r: float
    g: float
    b: float
    a: float = 1.0

    def __post_init__(self) -> None:
        if any(
            component < 0.0 or component > 1.0 for component in (self.r, self.g, self.b, self.a)
        ):
            msg = "color components must be normalized to the range 0..1"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, order=True)
class MidiNoteKey:
    channel: int
    note: int

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            msg = "MIDI channel must be in the range 0..15"
            raise ValueError(msg)
        if not 0 <= self.note <= 127:
            msg = "MIDI note must be in the range 0..127"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MidiStateFrame:
    notes: Mapping[MidiNoteKey, int]
    context: FrameContext
    source_node_id: UUID

    def __post_init__(self) -> None:
        copied: dict[MidiNoteKey, int] = {}
        for key, velocity in self.notes.items():
            if not 1 <= velocity <= 127:
                msg = "active MIDI note velocities must be in the range 1..127"
                raise ValueError(msg)
            copied[key] = velocity
        object.__setattr__(self, "notes", MappingProxyType(copied))


class NoDataType:
    """Type of the one explicit missing-runtime-value sentinel."""

    __slots__ = ()
    _instance: ClassVar[NoDataType | None] = None

    def __new__(cls) -> NoDataType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NoData"

    def __reduce__(self) -> str:
        return "NoData"


NoData: Final = NoDataType()

type ParameterValue = float | int | bool | str | ColorValue
type RuntimeValue = (
    ImageFrame | ChannelFrame | float | int | bool | ColorValue | MidiStateFrame | str | NoDataType
)


def clock_id_of(value: RuntimeValue) -> UUID | None:
    """Return the source clock for a dynamic value, or ``None`` for a static value."""

    if isinstance(value, (ImageFrame, ChannelFrame, MidiStateFrame)):
        return value.context.clock_id
    return None


def is_no_data(value: RuntimeValue) -> bool:
    return value is NoData
