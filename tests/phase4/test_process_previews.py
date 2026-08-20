"""End-to-end spawned-engine shared-memory preview tests."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from tools.generate_test_video import generate_test_video

from synesthesia_machine.contracts import ImagePreview, ValuePreview
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes.visualization import NOTE_VISUALIZER_TYPE_ID
from synesthesia_machine.runtime import ProcessEngineClient


@pytest.fixture
def process_client() -> Iterator[ProcessEngineClient]:
    client = ProcessEngineClient(
        request_timeout_s=1.5,
        activation_timeout_s=5.0,
        heartbeat_timeout_s=1.5,
        close_timeout_s=0.5,
    )
    yield client
    client.close()


def _video_preview_document(
    video_path: Path,
    *,
    width: int = 32,
    height: int = 24,
) -> tuple[GraphDocument, UUID, UUID, UUID]:
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(video_path)},
    )
    resize_id = document.add_node(
        "synmachine.image.resize",
        parameters={"width": width, "height": height},
    )
    # Fresh nodes are stamped at the definition's current implementation version.
    preview_id = document.add_node(
        "synmachine.visualization.display_image_data", implementation_version=2
    )
    document.add_connection(source_id, "image", resize_id, "image")
    document.add_connection(resize_id, "image", preview_id, "image")
    return document, source_id, resize_id, preview_id


def _wait_for_preview(
    client: ProcessEngineClient,
    preview_id: UUID,
    *,
    after_sequences: Mapping[tuple[UUID, str], int] | None = None,
    timeout_s: float = 3.0,
) -> ImagePreview:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        previews = client.poll_image_previews(after_sequences)
        match = next((preview for preview in previews if preview.owner_id == preview_id), None)
        if match is not None:
            return match
        time.sleep(0.02)
    pytest.fail(
        f"No shared-memory preview arrived for node {preview_id} within {timeout_s:.1f}s; "
        f"status={client.status()!r}, slots={client.preview_shared_memory_names()!r}, "
        f"sources={client.source_status()!r}, metrics={client.metrics()!r}"
    )


def _wait_for_value_preview(
    client: ProcessEngineClient,
    owner_id: UUID,
    *,
    timeout_s: float = 3.0,
) -> ValuePreview:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        previews = client.poll_value_previews()
        match = next((preview for preview in previews if preview.owner_id == owner_id), None)
        if match is not None:
            return match
        time.sleep(0.02)
    pytest.fail(
        f"No value preview arrived for producer {owner_id} within {timeout_s:.1f}s; "
        f"status={client.status()!r}, sources={client.source_status()!r}"
    )


def _assert_shared_memory_unlinked(name: str) -> None:
    try:
        shared_memory = SharedMemory(name=name)
    except FileNotFoundError:
        return
    shared_memory.close()
    pytest.fail(f"Shared-memory preview slot {name!r} remains available")


def _render_preview(
    client: ProcessEngineClient,
    document: GraphDocument,
    source_id: UUID,
    preview_id: UUID,
) -> ImagePreview:
    activation = client.activate(document.snapshot())
    assert activation.activated and activation.report.is_valid
    client.play(source_id)
    assert client.wait_until_idle(3.0)
    return _wait_for_preview(client, preview_id)


def test_child_publishes_immutable_preview_and_graceful_close_unlinks_slot(
    tmp_path: Path,
) -> None:
    video_path = generate_test_video(tmp_path / "graceful-preview.mp4", fps=60)
    document, source_id, resize_id, _ = _video_preview_document(video_path)
    client = ProcessEngineClient(close_timeout_s=0.5)

    preview = _render_preview(client, document, source_id, resize_id)

    assert (preview.width, preview.height, preview.channels) == (32, 24, 3)
    assert preview.data.shape == (24, 32, 3)
    assert preview.data.dtype == np.uint8
    assert preview.data.flags.c_contiguous
    assert not preview.data.flags.writeable
    assert preview.sequence > 0
    assert preview.tick_index > 0
    latest = {
        (item.owner_id, item.source_port_id): item.sequence for item in client.poll_image_previews()
    }
    assert latest[(resize_id, "image")] == preview.sequence
    assert len(latest) == 2
    assert client.poll_image_previews(latest) == ()
    slot_names = client.preview_shared_memory_names()
    assert len(slot_names) == 2

    client.close()
    client.close()

    assert client.preview_shared_memory_names() == ()
    for name in slot_names:
        _assert_shared_memory_unlinked(name)


def test_reactivation_replaces_generation_and_unlinks_previous_slot(
    tmp_path: Path,
    process_client: ProcessEngineClient,
) -> None:
    video_path = generate_test_video(tmp_path / "resized-preview.mp4", fps=60)
    document, source_id, resize_id, _ = _video_preview_document(video_path)
    first = _render_preview(process_client, document, source_id, resize_id)
    first_names = process_client.preview_shared_memory_names()
    assert len(first_names) == 2

    document.set_parameter(resize_id, "width", 16)
    document.set_parameter(resize_id, "height", 12)
    second = _render_preview(process_client, document, source_id, resize_id)
    second_names = process_client.preview_shared_memory_names()

    assert (first.width, first.height) == (32, 24)
    assert (second.width, second.height, second.channels) == (16, 12, 3)
    assert len(second_names) == 2
    assert second_names != first_names
    for name in first_names:
        _assert_shared_memory_unlinked(name)


def test_forced_child_termination_unlinks_parent_owned_preview_slot(
    tmp_path: Path,
) -> None:
    video_path = generate_test_video(tmp_path / "forced-preview.mp4", fps=60)
    document, source_id, resize_id, _ = _video_preview_document(video_path)
    client = ProcessEngineClient(close_timeout_s=0.5)
    try:
        _render_preview(client, document, source_id, resize_id)
        slot_names = client.preview_shared_memory_names()
        assert len(slot_names) == 2

        client.force_terminate()

        assert client.preview_shared_memory_names() == ()
        for name in slot_names:
            _assert_shared_memory_unlinked(name)
    finally:
        client.close()


def test_spawned_engine_delivers_scalar_value_preview_for_synesthesia_param(
    tmp_path: Path,
    process_client: ProcessEngineClient,
) -> None:
    """A scalar link pill anchored on a Number producer wired into a synesthesia
    connectable parameter must show a live value through the spawned-engine event
    transport.

    Regression: the client event loop only accepted the async event types listed in
    ``_ASYNC_EVENT_TYPES``; ``ValuePreviewsPublished`` was missing, so the spawned
    engine silently dropped every value preview (the in-process broker path was
    unaffected). The pill rendered but never received its number.
    """

    video_path = generate_test_video(tmp_path / "value-param-preview.mp4", fps=60)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video", parameters={"file_path": str(video_path)}
    )
    luminance_id = document.add_node("synmachine.image.to_luminance")
    pitch_id = document.add_node("synmachine.synesthesia.channel_to_pitch")
    note_id = document.add_node(NOTE_VISUALIZER_TYPE_ID)
    number_id = document.add_node("synmachine.utility.number", parameters={"float_value": 0.5})
    document.add_connection(source_id, "image", luminance_id, "image")
    document.add_connection(luminance_id, "channel", pitch_id, "value")
    document.add_connection(pitch_id, "midi", note_id, "midi")
    # Number -> synesthesia connectable parameter (the user's reported scenario).
    document.add_connection(number_id, "value", pitch_id, "minimum_a")

    # The UI forces the producer of a visible scalar pill to be a demand root, so
    # the engine computes it even though its only consumer is a parameter, not a port.
    activation = process_client.activate(document.snapshot(), demand_roots={note_id, number_id})
    assert activation.activated and activation.report.is_valid
    process_client.play(source_id)
    assert process_client.wait_until_idle(3.0)

    preview = _wait_for_value_preview(process_client, number_id)

    assert preview.owner_id == number_id
    assert preview.port_type == "FLOAT"
    assert preview.text == "0.5"
    assert preview.sequence > 0
    assert preview.tick_index > 0
