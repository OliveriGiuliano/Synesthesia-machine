"""Bounded UI-owned shared-memory slots for process image previews."""

from __future__ import annotations

from contextlib import suppress
from multiprocessing.shared_memory import SharedMemory
from struct import Struct
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import ImagePreview, freeze_uint8_preview
from synesthesia_machine.contracts.engine_messages import PreviewSlotDescriptor

# A sequence-lock header lets the UI reject a copy made while the child writes.
# write_version, preview_sequence, tick_index, generation, width, height, channels
_HEADER = Struct("<QQQQIII4x")
_READ_ATTEMPTS = 4


def preview_slot_size(width: int, height: int, channels: int) -> int:
    """Return the exact fixed allocation for one header and one uint8 frame."""

    _validate_dimensions(width, height, channels)
    return _HEADER.size + width * height * channels


class OwnedPreviewSlot:
    """UI-side owner; creates, copies from, unlinks, and idempotently closes a slot."""

    def __init__(self, descriptor: PreviewSlotDescriptor, shared_memory: SharedMemory) -> None:
        self.descriptor = descriptor
        self._shared_memory = shared_memory
        self._closed = False

    @classmethod
    def create(
        cls,
        *,
        owner_id: UUID,
        source_port_id: str,
        generation: int,
        width: int,
        height: int,
        channels: int,
    ) -> OwnedPreviewSlot:
        _validate_generation(generation)
        if not source_port_id:
            raise ValueError("preview slot source port must be non-empty")
        shared_memory = SharedMemory(
            create=True,
            size=preview_slot_size(width, height, channels),
        )
        buffer = _shared_memory_buffer(shared_memory)
        buffer[:] = b"\x00" * len(buffer)
        descriptor = PreviewSlotDescriptor(
            owner_id,
            source_port_id,
            shared_memory.name,
            generation,
            width,
            height,
            channels,
        )
        return cls(descriptor, shared_memory)

    @property
    def name(self) -> str:
        return self.descriptor.shared_memory_name

    def read(self) -> ImagePreview | None:
        """Return one immutable consistent copy, or None while empty/being written."""

        if self._closed:
            return None
        buffer = _shared_memory_buffer(self._shared_memory)
        descriptor = self.descriptor
        for _ in range(_READ_ATTEMPTS):
            before = _HEADER.unpack_from(buffer)
            write_version, sequence, tick_index, generation, width, height, channels = before
            if write_version == 0 or write_version % 2 or sequence == 0:
                continue
            if (
                generation != descriptor.generation
                or width != descriptor.width
                or height != descriptor.height
                or channels != descriptor.channels
            ):
                return None
            view = np.ndarray(
                (height, width, channels),
                dtype=np.uint8,
                buffer=buffer,
                offset=_HEADER.size,
            )
            data = freeze_uint8_preview(view)
            del view
            after = _HEADER.unpack_from(buffer)
            if before == after and after[0] % 2 == 0:
                return ImagePreview(
                    descriptor.owner_id,
                    descriptor.source_port_id,
                    sequence,
                    tick_index,
                    width,
                    height,
                    channels,
                    data,
                )
        return None

    def peek_sequence(self) -> int:
        """Return the newest committed preview sequence without copying a frame.

        UI pollers run at display cadence; the seqlock header is 48 bytes while
        read() copies the whole frame. Callers compare the sequence against
        their threshold and only pay for a full copy when a new frame arrived.
        A writer mid-update yields 0; the missed sequence is picked up on the
        next tick without data loss because the caller's threshold is untouched.
        """

        if self._closed:
            return 0
        buffer = _shared_memory_buffer(self._shared_memory)
        for _ in range(_READ_ATTEMPTS):
            header = _HEADER.unpack_from(buffer)
            if header[0] == 0 or header[0] % 2:
                continue
            if _HEADER.unpack_from(buffer) == header:
                return header[1]
        return 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(BufferError, OSError):
            self._shared_memory.close()
        with suppress(FileNotFoundError, OSError):
            self._shared_memory.unlink()


class AttachedPreviewSlot:
    """Engine-side non-owner attachment that writes without unlinking UI memory."""

    def __init__(self, descriptor: PreviewSlotDescriptor) -> None:
        _validate_generation(descriptor.generation)
        expected_size = preview_slot_size(
            descriptor.width,
            descriptor.height,
            descriptor.channels,
        )
        shared_memory = SharedMemory(name=descriptor.shared_memory_name)
        if shared_memory.size < expected_size:
            shared_memory.close()
            raise ValueError(
                f"Preview slot {descriptor.shared_memory_name!r} has {shared_memory.size} bytes; "
                f"expected at least {expected_size}"
            )
        self.descriptor = descriptor
        self._shared_memory = shared_memory
        self._write_version = 0
        self._closed = False

    def write(self, preview: ImagePreview) -> None:
        if self._closed:
            raise RuntimeError("Preview slot is closed")
        descriptor = self.descriptor
        if preview.owner_id != descriptor.owner_id:
            raise ValueError("Preview owner does not match the shared-memory slot")
        if preview.source_port_id != descriptor.source_port_id:
            raise ValueError("Preview source port does not match the shared-memory slot")
        if (
            preview.width != descriptor.width
            or preview.height != descriptor.height
            or preview.channels != descriptor.channels
        ):
            raise ValueError("Preview dimensions do not match the shared-memory slot")
        self._write_version += 1
        if self._write_version % 2 == 0:
            self._write_version += 1
        buffer = _shared_memory_buffer(self._shared_memory)
        _HEADER.pack_into(
            buffer,
            0,
            self._write_version,
            preview.sequence,
            preview.tick_index,
            descriptor.generation,
            descriptor.width,
            descriptor.height,
            descriptor.channels,
        )
        # Keep NumPy arrays away from the lifetime of the cross-process mapping. Windows
        # reported the old np.copyto(shared-memory ndarray, ...) path as a native
        # _multiarray_umath access violation. ImagePreview guarantees a contiguous uint8
        # payload, so a bounded buffer copy is both sufficient and safer during shutdown.
        payload = memoryview(preview.data).cast("B")
        try:
            payload_end = _HEADER.size + len(payload)
            buffer[_HEADER.size : payload_end] = payload
        finally:
            payload.release()
        self._write_version += 1
        _HEADER.pack_into(
            buffer,
            0,
            self._write_version,
            preview.sequence,
            preview.tick_index,
            descriptor.generation,
            descriptor.width,
            descriptor.height,
            descriptor.channels,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(BufferError, OSError):
            self._shared_memory.close()


def _validate_dimensions(width: int, height: int, channels: int) -> None:
    if width < 1 or height < 1:
        raise ValueError("preview dimensions must be positive")
    if channels not in (3, 4):
        raise ValueError("preview channels must be RGB or RGBA")


def _validate_generation(generation: int) -> None:
    if generation < 1:
        raise ValueError("preview generation must be positive")


def _shared_memory_buffer(shared_memory: SharedMemory) -> memoryview:
    buffer = shared_memory.buf
    if buffer is None:
        raise RuntimeError("shared-memory buffer is unavailable")
    return buffer


__all__ = ["AttachedPreviewSlot", "OwnedPreviewSlot", "preview_slot_size"]
