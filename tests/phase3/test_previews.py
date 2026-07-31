"""Visualizer definitions and Qt-free latest-preview broker tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    MidiNoteKey,
    MidiStateFrame,
    freeze_uint8_preview,
    read_only_float32,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import CachePolicy, ExecutionKind
from synesthesia_machine.nodes.visualization import (
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
from synesthesia_machine.runtime import ExecutionPlan, PortKey, PreviewBroker, TickResult

SOURCE_ID = UUID("00000000-0000-0000-0000-000000000601")
IMAGE_VISUALIZER_ID = UUID("00000000-0000-0000-0000-000000000602")
MIDI_SOURCE_ID = UUID("00000000-0000-0000-0000-000000000603")
NOTE_VISUALIZER_ID = UUID("00000000-0000-0000-0000-000000000604")


@dataclass(slots=True)
class _Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


def _context(tick_index: int) -> FrameContext:
    return FrameContext(SOURCE_ID, tick_index, tick_index - 1, 0.0, 1, None, False)


def _image(data: np.ndarray[tuple[int, ...], np.dtype[np.float32]], tick_index: int) -> ImageFrame:
    return ImageFrame(
        read_only_float32(data),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        _context(tick_index),
        FrameProvenance(SOURCE_ID, "test"),
    )


def _image_plan(*, preview_fps: int = 30, max_dimension: int = 800) -> ExecutionPlan:
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={"file_path": "unused.mp4"},
    )
    document.add_node(
        DISPLAY_IMAGE_DATA_TYPE_ID,
        node_id=IMAGE_VISUALIZER_ID,
        parameters={"preview_fps": preview_fps, "max_dimension": max_dimension},
    )
    document.add_connection(SOURCE_ID, "image", IMAGE_VISUALIZER_ID, "image")
    result = GraphCompiler(create_application_registry()).compile(document.snapshot())
    assert result.plan is not None
    return result.plan


def _note_plan() -> ExecutionPlan:
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={"file_path": "unused.mp4"},
    )
    luminance_id = document.add_node("synmachine.image.to_luminance")
    document.add_node(
        "synmachine.synesthesia.channel_to_pitch",
        node_id=MIDI_SOURCE_ID,
    )
    document.add_node(NOTE_VISUALIZER_TYPE_ID, node_id=NOTE_VISUALIZER_ID)
    document.add_connection(SOURCE_ID, "image", luminance_id, "image")
    document.add_connection(luminance_id, "channel", MIDI_SOURCE_ID, "value")
    document.add_connection(MIDI_SOURCE_ID, "midi", NOTE_VISUALIZER_ID, "midi")
    result = GraphCompiler(create_application_registry()).compile(document.snapshot())
    assert result.plan is not None
    return result.plan


def test_visualizer_definitions_are_permanent_demand_roots_with_phase3_defaults() -> None:
    registry = create_application_registry()
    image = registry.require(DISPLAY_IMAGE_DATA_TYPE_ID)
    notes = registry.require(NOTE_VISUALIZER_TYPE_ID)

    assert image.execution_kind is notes.execution_kind is ExecutionKind.VISUALIZER
    assert image.cache_policy is notes.cache_policy is CachePolicy.NEVER
    values, errors = image.parameter_values({})
    assert not errors
    assert values["preview_fps"] == 30
    assert values["max_dimension"] == 800
    assert values["fit_mode"] == "CONTAIN"
    assert values["checkerboard_alpha"] is True


def test_image_preview_is_sanitized_immutable_resized_and_sequence_polled() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    broker.configure(_image_plan(max_dimension=800))
    data = np.zeros((500, 1000, 3), dtype=np.float32)
    data[0, 0] = (np.nan, np.inf, -np.inf)

    broker.publish(TickResult({PortKey(SOURCE_ID, "image"): _image(data, 1)}, (), {}))

    previews = broker.poll_images()
    assert len(previews) == 1
    preview = previews[0]
    assert (preview.width, preview.height, preview.channels) == (800, 400, 3)
    assert preview.sequence == 1 and preview.tick_index == 1
    assert preview.data.dtype == np.uint8
    assert preview.data.flags.c_contiguous and not preview.data.flags.writeable
    assert np.isfinite(preview.data).all()
    assert broker.poll_images({IMAGE_VISUALIZER_ID: 1}) == ()


def test_image_preview_throttles_to_configured_rate_and_coalesces_latest_tick() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    broker.configure(_image_plan(preview_fps=30))

    broker.publish(
        TickResult(
            {PortKey(SOURCE_ID, "image"): _image(np.zeros((2, 2, 3), dtype=np.float32), 1)},
            (),
            {},
        )
    )
    clock.value = 1.0 / 60.0
    broker.publish(
        TickResult(
            {PortKey(SOURCE_ID, "image"): _image(np.ones((2, 2, 3), dtype=np.float32), 2)},
            (),
            {},
        )
    )
    assert broker.poll_images()[0].tick_index == 1

    clock.value = 1.0 / 30.0
    broker.publish(
        TickResult(
            {PortKey(SOURCE_ID, "image"): _image(np.ones((2, 2, 3), dtype=np.float32), 3)},
            (),
            {},
        )
    )
    preview = broker.poll_images({IMAGE_VISUALIZER_ID: 1})[0]
    assert preview.sequence == 2 and preview.tick_index == 3
    assert np.all(preview.data == 255)


def test_note_preview_is_compact_sorted_60hz_and_replaced_on_activation() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    plan = _note_plan()
    broker.configure(plan)
    first = MidiStateFrame(
        {MidiNoteKey(2, 72): 40, MidiNoteKey(0, 60): 100},
        _context(1),
        MIDI_SOURCE_ID,
    )
    broker.publish(TickResult({PortKey(MIDI_SOURCE_ID, "midi"): first}, (), {}))
    preview = broker.poll_notes()[0]
    assert [(note.channel, note.note, note.velocity) for note in preview.notes] == [
        (0, 60, 100),
        (2, 72, 40),
    ]

    clock.value = 1.0 / 120.0
    second = MidiStateFrame({}, _context(2), MIDI_SOURCE_ID)
    broker.publish(TickResult({PortKey(MIDI_SOURCE_ID, "midi"): second}, (), {}))
    assert broker.poll_notes()[0].tick_index == 1

    clock.value = 1.0 / 60.0
    broker.publish(TickResult({PortKey(MIDI_SOURCE_ID, "midi"): second}, (), {}))
    assert broker.poll_notes({NOTE_VISUALIZER_ID: 1})[0].notes == ()

    broker.configure(plan)
    assert broker.poll_images() == ()
    assert broker.poll_notes() == ()


def test_reconfiguration_discards_an_in_flight_publication() -> None:
    conversion_started = threading.Event()
    continue_conversion = threading.Event()

    def blocking_converter(image: ImageFrame, max_dimension: int) -> NDArray[np.uint8]:
        del max_dimension
        conversion_started.set()
        assert continue_conversion.wait(timeout=2.0)
        return freeze_uint8_preview(np.zeros_like(image.data, dtype=np.uint8))

    broker = PreviewBroker(image_converter=blocking_converter)
    plan = _image_plan()
    broker.configure(plan)
    publication = threading.Thread(
        target=broker.publish,
        args=(
            TickResult(
                {PortKey(SOURCE_ID, "image"): _image(np.zeros((2, 2, 3), dtype=np.float32), 1)},
                (),
                {},
            ),
        ),
    )
    publication.start()
    assert conversion_started.wait(timeout=2.0)

    broker.configure(plan)
    continue_conversion.set()
    publication.join(timeout=2.0)

    assert not publication.is_alive()
    assert broker.poll_images() == ()
