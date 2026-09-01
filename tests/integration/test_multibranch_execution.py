"""Regression coverage for source fan-out through independent image-to-MIDI paths."""

from __future__ import annotations

from uuid import UUID

import numpy as np

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    MidiStateFrame,
    read_only_float32,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.runtime import PortKey, Scheduler

SOURCE_ID = UUID("00000000-0000-0000-0000-000000008001")
DIRECT_LUMINANCE_ID = UUID("00000000-0000-0000-0000-000000008002")
DIRECT_PITCH_ID = UUID("00000000-0000-0000-0000-000000008003")
LEVELS_ID = UUID("00000000-0000-0000-0000-000000008004")
ROTATE_ID = UUID("00000000-0000-0000-0000-000000008005")
ROTATED_LUMINANCE_ID = UUID("00000000-0000-0000-0000-000000008006")
ROTATED_PITCH_ID = UUID("00000000-0000-0000-0000-000000008007")
MERGE_ID = UUID("00000000-0000-0000-0000-000000008008")
VISUALIZER_ID = UUID("00000000-0000-0000-0000-000000008009")


def test_fan_out_image_branches_merge_to_midi_after_levels_black_point() -> None:
    """A low input level must not poison the sibling MIDI branch with NaNs."""

    document = GraphDocument()
    document.add_node("synmachine.input.load_video", node_id=SOURCE_ID)
    document.add_node("synmachine.image.to_luminance", node_id=DIRECT_LUMINANCE_ID)
    document.add_node("synmachine.synesthesia.channel_to_pitch", node_id=DIRECT_PITCH_ID)
    document.add_node(
        "synmachine.image.colour_levels",
        node_id=LEVELS_ID,
        parameters={
            "input_black": 0.3,
            "input_white": 1.58,
            "gamma": 1.18,
        },
    )
    document.add_node(
        "synmachine.image.rotate",
        node_id=ROTATE_ID,
        parameters={"angle_degrees": 45.0, "centre_x": 0.7778},
    )
    document.add_node("synmachine.image.to_luminance", node_id=ROTATED_LUMINANCE_ID)
    document.add_node("synmachine.synesthesia.channel_to_pitch", node_id=ROTATED_PITCH_ID)
    document.add_node("synmachine.utility.midi_merge", node_id=MERGE_ID)
    document.add_node("synmachine.visualization.note_visualizer", node_id=VISUALIZER_ID)

    document.add_connection(SOURCE_ID, "image", DIRECT_LUMINANCE_ID, "image")
    document.add_connection(DIRECT_LUMINANCE_ID, "channel", DIRECT_PITCH_ID, "value")
    document.add_connection(DIRECT_PITCH_ID, "midi", MERGE_ID, "midi_1")
    document.add_connection(SOURCE_ID, "image", LEVELS_ID, "image")
    document.add_connection(LEVELS_ID, "image", ROTATE_ID, "image")
    document.add_connection(ROTATE_ID, "image", ROTATED_LUMINANCE_ID, "image")
    document.add_connection(ROTATED_LUMINANCE_ID, "channel", ROTATED_PITCH_ID, "value")
    document.add_connection(ROTATED_PITCH_ID, "midi", MERGE_ID, "midi_2")
    document.add_connection(MERGE_ID, "midi", VISUALIZER_ID, "midi")

    compiled = GraphCompiler(create_application_registry()).compile(document.snapshot())
    assert compiled.report.is_valid and compiled.plan is not None

    context = FrameContext(SOURCE_ID, 1, 0, 0.0, 1, None, False)
    image = ImageFrame(
        read_only_float32(np.full((8, 8, 3), 0.2, dtype=np.float32)),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(SOURCE_ID, "test"),
    )
    scheduler = Scheduler(compiled.plan)
    try:
        tick = scheduler.execute_tick(
            context,
            source_values={
                PortKey(SOURCE_ID, "image"): image,
                PortKey(SOURCE_ID, "processed_index"): 0,
            },
        )
    finally:
        scheduler.close()

    assert tick.errors == ()
    assert isinstance(tick.values[PortKey(DIRECT_PITCH_ID, "midi")], MidiStateFrame)
    assert isinstance(tick.values[PortKey(ROTATED_PITCH_ID, "midi")], MidiStateFrame)
    merged = tick.values[PortKey(MERGE_ID, "midi")]
    assert isinstance(merged, MidiStateFrame)
    assert merged.notes
    assert tick.invocation_counts[VISUALIZER_ID] == 1
