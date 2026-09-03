"""UI-owned shared-memory preview slot tests."""

from multiprocessing.shared_memory import SharedMemory
from uuid import uuid4

import numpy as np
import pytest

from synesthesia_machine.contracts import ImagePreview, freeze_uint8_preview
from synesthesia_machine.runtime import (
    AttachedPreviewSlot,
    OwnedPreviewSlot,
    ProcessEngineClient,
)


def test_slot_round_trip_copies_uint8_metadata_and_unlinks_idempotently() -> None:
    node_id = uuid4()
    owner = OwnedPreviewSlot.create(
        owner_id=node_id,
        source_port_id="image",
        generation=2,
        width=4,
        height=3,
        channels=3,
    )
    name = owner.name
    attachment = AttachedPreviewSlot(owner.descriptor)
    data = freeze_uint8_preview(np.arange(36, dtype=np.uint8).reshape((3, 4, 3)))
    try:
        assert owner.read() is None
        attachment.write(ImagePreview(node_id, "image", 7, 11, 4, 3, 3, data))

        result = owner.read()

        assert result is not None
        assert (result.sequence, result.tick_index) == (7, 11)
        assert not result.data.flags.writeable
        np.testing.assert_array_equal(result.data, data)
    finally:
        attachment.close()
        attachment.close()
        owner.close()
        owner.close()

    with pytest.raises(FileNotFoundError):
        SharedMemory(name=name)


def test_slot_rejects_wrong_shape_without_corrupting_previous_frame() -> None:
    node_id = uuid4()
    owner = OwnedPreviewSlot.create(
        owner_id=node_id,
        source_port_id="image",
        generation=1,
        width=2,
        height=2,
        channels=3,
    )
    attachment = AttachedPreviewSlot(owner.descriptor)
    first = freeze_uint8_preview(np.full((2, 2, 3), 17, dtype=np.uint8))
    wrong = freeze_uint8_preview(np.zeros((3, 2, 3), dtype=np.uint8))
    try:
        attachment.write(ImagePreview(node_id, "image", 1, 1, 2, 2, 3, first))
        with pytest.raises(ValueError, match="dimensions"):
            attachment.write(ImagePreview(node_id, "image", 2, 2, 2, 3, 3, wrong))

        result = owner.read()

        assert result is not None and result.sequence == 1
        np.testing.assert_array_equal(result.data, first)
    finally:
        attachment.close()
        owner.close()


def test_peek_sequence_tracks_committed_frames_without_reading_frames() -> None:
    node_id = uuid4()
    owner = OwnedPreviewSlot.create(
        owner_id=node_id,
        source_port_id="image",
        generation=1,
        width=2,
        height=2,
        channels=3,
    )
    attachment = AttachedPreviewSlot(owner.descriptor)
    first = freeze_uint8_preview(np.full((2, 2, 3), 1, dtype=np.uint8))
    second = freeze_uint8_preview(np.full((2, 2, 3), 2, dtype=np.uint8))
    try:
        assert owner.peek_sequence() == 0
        attachment.write(ImagePreview(node_id, "image", 1, 1, 2, 2, 3, first))
        assert owner.peek_sequence() == 1
        attachment.write(ImagePreview(node_id, "image", 2, 2, 2, 2, 3, second))
        assert owner.peek_sequence() == 2
    finally:
        attachment.close()
        owner.close()


def test_poll_image_previews_copies_only_frames_past_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ProcessEngineClient(auto_start=False)
    owner = OwnedPreviewSlot.create(
        owner_id=uuid4(),
        source_port_id="image",
        generation=1,
        width=2,
        height=2,
        channels=3,
    )
    attachment = AttachedPreviewSlot(owner.descriptor)
    key = (owner.descriptor.owner_id, "image")
    read_calls = 0
    original_read = owner.read

    def counting_read() -> object:
        nonlocal read_calls
        read_calls += 1
        return original_read()

    try:
        # Inject the slot without a live engine: the poll loop only touches the
        # owned-slot table, so the copy-skip behaviour is testable in isolation.
        monkeypatch.setitem(client._image_slots, key, owner)  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(owner, "read", counting_read)

        first_frame = freeze_uint8_preview(np.full((2, 2, 3), 1, dtype=np.uint8))
        attachment.write(
            ImagePreview(owner.descriptor.owner_id, "image", 1, 1, 2, 2, 3, first_frame)
        )
        assert [preview.sequence for preview in client.poll_image_previews()] == [1]
        assert read_calls == 1

        # Idle slot: the seqlock peek says "no new frame", so no frame copy.
        assert client.poll_image_previews({key: 1}) == ()
        assert read_calls == 1

        second_frame = freeze_uint8_preview(np.full((2, 2, 3), 2, dtype=np.uint8))
        attachment.write(
            ImagePreview(owner.descriptor.owner_id, "image", 2, 2, 2, 2, 3, second_frame)
        )
        assert [preview.sequence for preview in client.poll_image_previews({key: 1})] == [2]
        assert read_calls == 2
    finally:
        monkeypatch.undo()
        attachment.close()
        owner.close()
        client.close()
