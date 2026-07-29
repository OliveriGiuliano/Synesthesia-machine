"""Runtime-value contract tests."""

from __future__ import annotations

import pickle
from collections.abc import MutableMapping
from types import MappingProxyType
from typing import cast
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameProvenance,
    ImageFrame,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    NoDataType,
    read_only_float32,
)
from tests.phase1.helpers import frame_context

CLOCK_ID = UUID("00000000-0000-0000-0000-000000000101")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000102")


def test_no_data_is_a_distinct_singleton() -> None:
    assert NoData is not None
    assert repr(NoData) == "NoData"
    assert isinstance(NoData, NoDataType)
    assert NoDataType() is NoData
    assert pickle.loads(pickle.dumps(NoData)) is NoData


def test_runtime_arrays_are_c_contiguous_float32_and_read_only() -> None:
    source = np.arange(24, dtype=np.float32).reshape(2, 3, 4)[:, ::-1]
    image_data = read_only_float32(source)
    image = ImageFrame(
        image_data,
        ColorSpace.LINEAR_RGB,
        ("r", "g", "b", "a"),
        AlphaMode.STRAIGHT,
        frame_context(clock_id=CLOCK_ID),
        FrameProvenance(SOURCE_ID, "test"),
    )
    channel_data = read_only_float32(np.ones((2, 3), dtype=np.float32))
    channel = ChannelFrame(
        channel_data,
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        image.context,
    )

    assert image.data.dtype == np.float32
    assert image.data.flags.c_contiguous
    assert not image.data.flags.writeable
    assert not channel.data.flags.writeable
    with pytest.raises(ValueError):
        image.data[0, 0, 0] = 0.0


def test_runtime_values_reject_writeable_arrays_and_copy_midi_notes() -> None:
    context = frame_context(clock_id=CLOCK_ID)
    with pytest.raises(ValueError, match="read-only"):
        ChannelFrame(
            np.ones((2, 2), dtype=np.float32),
            ChannelSemantic.GENERIC,
            0.0,
            1.0,
            False,
            context,
        )

    notes = {MidiNoteKey(0, 60): 100}
    state = MidiStateFrame(notes, context, SOURCE_ID)
    notes[MidiNoteKey(0, 61)] = 90
    assert state.notes == MappingProxyType({MidiNoteKey(0, 60): 100})
    mutable_notes = cast(MutableMapping[MidiNoteKey, int], state.notes)
    with pytest.raises(TypeError):
        mutable_notes[MidiNoteKey(0, 62)] = 80
