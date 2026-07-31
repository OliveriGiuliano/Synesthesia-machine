"""UI-owned shared-memory preview slot tests."""

from multiprocessing.shared_memory import SharedMemory
from uuid import uuid4

import numpy as np
import pytest

from synesthesia_machine.contracts import ImagePreview, freeze_uint8_preview
from synesthesia_machine.runtime import AttachedPreviewSlot, OwnedPreviewSlot


def test_slot_round_trip_copies_uint8_metadata_and_unlinks_idempotently() -> None:
    node_id = uuid4()
    owner = OwnedPreviewSlot.create(
        node_id=node_id,
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
        attachment.write(ImagePreview(node_id, 7, 11, 4, 3, 3, data))

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
        node_id=node_id,
        generation=1,
        width=2,
        height=2,
        channels=3,
    )
    attachment = AttachedPreviewSlot(owner.descriptor)
    first = freeze_uint8_preview(np.full((2, 2, 3), 17, dtype=np.uint8))
    wrong = freeze_uint8_preview(np.zeros((3, 2, 3), dtype=np.uint8))
    try:
        attachment.write(ImagePreview(node_id, 1, 1, 2, 2, 3, first))
        with pytest.raises(ValueError, match="dimensions"):
            attachment.write(ImagePreview(node_id, 2, 2, 2, 3, 3, wrong))

        result = owner.read()

        assert result is not None and result.sequence == 1
        np.testing.assert_array_equal(result.data, first)
    finally:
        attachment.close()
        owner.close()
