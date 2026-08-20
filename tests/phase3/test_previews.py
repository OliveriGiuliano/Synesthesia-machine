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
    PortType,
    freeze_uint8_preview,
    read_only_float32,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import CachePolicy, ExecutionKind
from synesthesia_machine.nodes.visualization import (
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    PreviewBroker,
    TickResult,
)

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


def _image_plan() -> ExecutionPlan:
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={"file_path": "unused.mp4"},
    )
    # Fresh nodes are stamped at the definition's current implementation version.
    document.add_node(
        DISPLAY_IMAGE_DATA_TYPE_ID,
        node_id=IMAGE_VISUALIZER_ID,
        implementation_version=2,
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


def _pill_only_image_plan() -> ExecutionPlan:
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={"file_path": "unused.mp4"},
    )
    resize_id = document.add_node(
        "synmachine.image.resize",
        parameters={"width": 2, "height": 2},
    )
    document.add_connection(SOURCE_ID, "image", resize_id, "image")
    result = GraphCompiler(create_application_registry()).compile(
        document.snapshot(), demand_roots=(resize_id,)
    )
    assert result.plan is not None
    return result.plan


def _many_image_target_plan(count: int) -> ExecutionPlan:
    registry = create_application_registry()
    port_ids = tuple(f"image_{index}" for index in range(count))
    producer = CompiledNode(
        SOURCE_ID,
        registry.require("synmachine.input.load_video"),
        output_types={port_id: PortType.IMAGE for port_id in port_ids},
    )
    consumers = tuple(
        CompiledNode(
            UUID(int=0x700 + index),
            registry.require(DISPLAY_IMAGE_DATA_TYPE_ID),
            input_bindings={"image": InputBinding(PortKey(SOURCE_ID, port_id))},
            input_types={"image": PortType.IMAGE},
        )
        for index, port_id in enumerate(port_ids)
    )
    return ExecutionPlan(
        UUID("00000000-0000-0000-0000-000000000699"),
        1,
        (producer, *consumers),
        frozenset(consumer.node_id for consumer in consumers),
    )


def test_visualizer_definitions_are_permanent_demand_roots_with_phase3_defaults() -> None:
    registry = create_application_registry()
    image = registry.require(DISPLAY_IMAGE_DATA_TYPE_ID)
    notes = registry.require(NOTE_VISUALIZER_TYPE_ID)

    assert image.execution_kind is notes.execution_kind is ExecutionKind.VISUALIZER
    assert image.cache_policy is notes.cache_policy is CachePolicy.NEVER
    values, errors = image.parameter_values({})
    assert not errors
    assert values["fit_mode"] == "CONTAIN"
    assert values["checkerboard_alpha"] is True


def test_image_preview_is_sanitized_immutable_resized_and_sequence_polled() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    broker.configure(_image_plan())
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
    assert preview.owner_id == SOURCE_ID
    assert broker.poll_images({(SOURCE_ID, "image"): 1}) == ()


def test_image_preview_throttles_to_configured_rate_and_coalesces_latest_tick() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    broker.configure(_image_plan())

    broker.publish(
        TickResult(
            {PortKey(SOURCE_ID, "image"): _image(np.zeros((2, 2, 3), dtype=np.float32), 1)},
            (),
            {},
        )
    )
    assert broker.preview_fps() == 1.0
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
    preview = broker.poll_images({(SOURCE_ID, "image"): 1})[0]
    assert preview.sequence == 2 and preview.tick_index == 3
    assert np.all(preview.data == 255)
    assert broker.preview_fps() == 2.0
    clock.value = 2.0
    assert broker.preview_fps() == 0.0


def test_preview_fps_counts_all_publications_above_the_old_fixed_history_cap() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    plan = _many_image_target_plan(9)
    broker.configure(plan)

    for tick_index in range(30):
        clock.value = tick_index / 30.0
        frame = _image(np.zeros((1, 1, 3), dtype=np.float32), tick_index + 1)
        broker.publish(
            TickResult(
                {PortKey(SOURCE_ID, f"image_{port_index}"): frame for port_index in range(9)},
                (),
                {},
            )
        )

    assert broker.preview_fps() == 270.0
    clock.value = 2.0
    assert broker.preview_fps() == 0.0


def test_pill_only_image_preview_uses_lower_background_cadence() -> None:
    clock = _Clock()
    broker = PreviewBroker(monotonic=clock)
    broker.configure(_pill_only_image_plan())

    first = _image(np.zeros((2, 2, 3), dtype=np.float32), 1)
    broker.publish(TickResult({PortKey(SOURCE_ID, "image"): first}, (), {}))
    assert broker.poll_images()[0].tick_index == 1

    clock.value = 1.0 / 30.0
    second = _image(np.ones((2, 2, 3), dtype=np.float32), 2)
    broker.publish(TickResult({PortKey(SOURCE_ID, "image"): second}, (), {}))
    assert broker.poll_images()[0].tick_index == 1

    clock.value = 1.0 / 15.0
    broker.publish(TickResult({PortKey(SOURCE_ID, "image"): second}, (), {}))
    assert broker.poll_images()[0].tick_index == 2


def test_aliased_immutable_frames_share_one_conversion_with_separate_routes() -> None:
    calls = 0

    def converter(image: ImageFrame, max_dimension: int) -> NDArray[np.uint8]:
        nonlocal calls
        del max_dimension
        calls += 1
        return freeze_uint8_preview(np.zeros_like(image.data, dtype=np.uint8))

    broker = PreviewBroker(image_converter=converter)
    broker.configure(_many_image_target_plan(2))
    frame = _image(np.zeros((2, 2, 3), dtype=np.float32), 1)
    broker.publish(
        TickResult(
            {
                PortKey(SOURCE_ID, "image_0"): frame,
                PortKey(SOURCE_ID, "image_1"): frame,
            },
            (),
            {},
        )
    )

    assert calls == 1
    assert {(preview.owner_id, preview.source_port_id) for preview in broker.poll_images()} == {
        (SOURCE_ID, "image_0"),
        (SOURCE_ID, "image_1"),
    }


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


def test_stamped_stale_publication_is_rejected_before_conversion() -> None:
    calls = 0

    def converter(image: ImageFrame, max_dimension: int) -> NDArray[np.uint8]:
        nonlocal calls
        del max_dimension
        calls += 1
        return freeze_uint8_preview(np.zeros_like(image.data, dtype=np.uint8))

    broker = PreviewBroker(image_converter=converter)
    plan = _image_plan()
    broker.configure(plan)
    stale_generation = broker.generation
    broker.configure(plan)

    broker.publish(
        TickResult(
            {PortKey(SOURCE_ID, "image"): _image(np.zeros((2, 2, 3), dtype=np.float32), 1)},
            (),
            {},
        ),
        expected_generation=stale_generation,
    )

    assert calls == 0
    assert broker.poll_images() == ()
