"""Runtime-value contract tests."""

from __future__ import annotations

import pickle
from collections.abc import MutableMapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast
from uuid import UUID

import numpy as np
import pytest
from tests.support.graph_factories import frame_context, make_definition

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    ColorValue,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    NoDataType,
    PortType,
    read_only_float32,
)
from synesthesia_machine.nodes import ParameterSpec

CLOCK_ID = UUID("00000000-0000-0000-0000-000000000101")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000102")


def test_node_definition_rejects_input_parameter_id_collisions() -> None:
    definition = make_definition("test.collision", input_type=PortType.FLOAT)

    with pytest.raises(ValueError, match="share stable IDs: value"):
        replace(
            definition,
            parameters=(ParameterSpec("value", "Value", PortType.FLOAT, 0.0),),
        )


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


@pytest.mark.parametrize("source_time", [float("nan"), float("inf"), float("-inf")])
def test_frame_context_rejects_non_finite_source_time(source_time: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        FrameContext(CLOCK_ID, 1, 0, source_time, 1, None, False)


def test_scalar_descriptors_and_parameters_reject_non_finite_values() -> None:
    context = frame_context(clock_id=CLOCK_ID)
    data = read_only_float32(np.ones((1, 1), dtype=np.float32))
    with pytest.raises(ValueError, match="nominal range must be finite"):
        ChannelFrame(data, ChannelSemantic.GENERIC, float("nan"), 1.0, False, context)
    with pytest.raises(ValueError, match="finite"):
        ColorValue(float("nan"), 0.0, 0.0)
    with pytest.raises(ValueError, match="Invalid default"):
        ParameterSpec("gain", "Gain", PortType.FLOAT, float("nan"))

    spec = ParameterSpec("gain", "Gain", PortType.FLOAT, 1.0, connectable=True)
    for invalid in (float("nan"), float("inf"), float("-inf")):
        assert spec.validate(invalid) == "must be finite"
        with pytest.raises(ValueError, match="finite"):
            spec.connected_value(invalid)


def test_parameter_constraints_clamp_and_snap_authored_and_connected_values() -> None:
    odd = ParameterSpec(
        "kernel_width",
        "Kernel width",
        PortType.INT,
        3,
        minimum=1,
        maximum=9,
        step=2,
    )

    assert odd.validate(4) == "must use increments of 2 from 1"
    assert odd.sanitize_value(-20) == 1
    assert odd.sanitize_value(4) == 5
    assert odd.sanitize_value(100) == 9
    assert odd.connected_value(4.0) == 5
    with pytest.raises(ValueError, match="positive integer"):
        ParameterSpec("bad", "Bad", PortType.INT, 1, step=0)
