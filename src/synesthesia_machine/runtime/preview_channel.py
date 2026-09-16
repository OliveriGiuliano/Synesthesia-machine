"""Bounded preview transport across the UI/engine process boundary.

The engine publishes the latest preview frame for each producing port through a
small ``PreviewTransport`` interface; the UI polls it. Two adapters implement
the interface:

- ``InMemoryPreviewTransport`` — in-process direct handoff. A bounded
  latest-frame store shared between the engine worker thread and the UI in a
  single process; no shared memory is involved.
- the shared-memory sequence-lock pair (``SharedMemoryPreviewWriter`` for the
  child engine, ``SharedMemoryPreviewReader`` for the UI). The UI owns
  sequence-locked slots and the child attaches and writes, so a preview frame
  crosses the process boundary without ever copying full-resolution data.

Both adapters share the sequence-lock slot layout, the slot lifetime
(create/attach/close/unlink), and the "latest frame wins" recycling, so a
maintainer changes the protocol in one place and it applies identically whether
the engine runs in-process or in the spawned child. The module is Qt-free and
is imported by both processes.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from multiprocessing.shared_memory import SharedMemory
from struct import Struct
from threading import Lock
from typing import Protocol
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import ImagePreview, freeze_uint8_preview
from synesthesia_machine.contracts.engine_messages import PreviewSlotDescriptor

type PreviewSlotKey = tuple[UUID, str]

# A sequence-lock header lets the UI reject a copy made while the child writes.
# write_version, preview_sequence, tick_index, generation, width, height, channels
_HEADER = Struct("<QQQQIII4x")
_READ_ATTEMPTS = 4


def preview_slot_size(width: int, height: int, channels: int) -> int:
    """Return the exact fixed allocation for one header and one uint8 frame."""

    _validate_dimensions(width, height, channels)
    return _HEADER.size + width * height * channels


class PreviewTransport(Protocol):
    """Seam between the engine publisher and the UI consumer for image previews.

    Implementations are adapters for one process-placement: in-process direct
    (``InMemoryPreviewTransport``) or the shared-memory sequence-lock pair
    (``SharedMemoryPreviewWriter`` / ``SharedMemoryPreviewReader``). The
    publisher stores or writes the latest frame per producing port; the
    consumer polls for frames newer than its sequence thresholds.
    """

    def publish_image(self, owner_id: UUID, source_port_id: str, preview: ImagePreview) -> None:
        """Store or write the latest frame for one producing port."""
        ...

    def poll_images(
        self, after_sequences: Mapping[PreviewSlotKey, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        """Return frames newer than the per-key sequence thresholds, ordered."""
        ...

    def peek_sequences(self) -> Mapping[PreviewSlotKey, int]:
        """Return the newest committed sequence per key without copying a frame."""
        ...

    def clear(self) -> None:
        """Drop all retained previews (e.g. after activation or a stop)."""
        ...

    def close(self) -> None:
        """Release all resources; idempotent."""
        ...


class InMemoryPreviewTransport:
    """In-process adapter: a bounded latest-frame store in one process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._previews: dict[PreviewSlotKey, ImagePreview] = {}

    def publish_image(self, owner_id: UUID, source_port_id: str, preview: ImagePreview) -> None:
        with self._lock:
            self._previews[(owner_id, source_port_id)] = preview

    def poll_images(
        self, after_sequences: Mapping[PreviewSlotKey, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        with self._lock:
            return tuple(
                preview
                for key, preview in sorted(
                    self._previews.items(), key=lambda item: (str(item[0][0]), item[0][1])
                )
                if preview.sequence > thresholds.get(key, 0)
            )

    def peek_sequences(self) -> Mapping[PreviewSlotKey, int]:
        with self._lock:
            return {key: preview.sequence for key, preview in self._previews.items()}

    def clear(self) -> None:
        with self._lock:
            self._previews.clear()

    def close(self) -> None:
        self.clear()


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


class SharedMemoryPreviewReader:
    """UI (owner) face of the shared-memory adapter.

    Owns the sequence-locked slots the child attaches to. ``create_slot`` is
    driven by the engine's ``PreviewFormatChanged`` announcements; ``poll_images``
    and ``peek_sequences`` read them at display cadence.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._slots: dict[PreviewSlotKey, OwnedPreviewSlot] = {}

    def create_slot(
        self,
        *,
        owner_id: UUID,
        source_port_id: str,
        generation: int,
        width: int,
        height: int,
        channels: int,
    ) -> OwnedPreviewSlot:
        replacement = OwnedPreviewSlot.create(
            owner_id=owner_id,
            source_port_id=source_port_id,
            generation=generation,
            width=width,
            height=height,
            channels=channels,
        )
        key = (owner_id, source_port_id)
        with self._lock:
            previous = self._slots.get(key)
            self._slots[key] = replacement
        if previous is not None:
            previous.close()
        return replacement

    def publish_image(self, owner_id: UUID, source_port_id: str, preview: ImagePreview) -> None:
        # The UI face never publishes; present for the uniform interface.
        return None

    def poll_images(
        self, after_sequences: Mapping[PreviewSlotKey, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        previews: list[ImagePreview] = []
        with self._lock:
            for key, slot in sorted(
                self._slots.items(), key=lambda item: (str(item[0][0]), item[0][1])
            ):
                threshold = thresholds.get(key, 0)
                if slot.peek_sequence() <= threshold:
                    continue
                preview = slot.read()
                if preview is not None and preview.sequence > threshold:
                    previews.append(preview)
        return tuple(previews)

    def peek_sequences(self) -> Mapping[PreviewSlotKey, int]:
        with self._lock:
            return {key: slot.peek_sequence() for key, slot in self._slots.items()}

    def current_descriptor(
        self, owner_id: UUID, source_port_id: str
    ) -> PreviewSlotDescriptor | None:
        """Return the descriptor of the slot currently owned for this target."""

        with self._lock:
            slot = self._slots.get((owner_id, source_port_id))
        return slot.descriptor if slot is not None else None

    def drop_slot(self, owner_id: UUID, source_port_id: str) -> None:
        """Close and forget the owned slot for this target (failed configuration)."""

        with self._lock:
            slot = self._slots.pop((owner_id, source_port_id), None)
        if slot is not None:
            slot.close()

    def shared_memory_names(self) -> tuple[str, ...]:
        with self._lock:
            ordered = sorted(self._slots.items(), key=lambda item: (str(item[0][0]), item[0][1]))
            return tuple(slot.name for _, slot in ordered)

    def clear(self) -> None:
        with self._lock:
            for slot in self._slots.values():
                slot.close()
            self._slots.clear()

    def close(self) -> None:
        self.clear()


class SharedMemoryPreviewWriter:
    """Child (engine) face of the shared-memory adapter.

    The engine broker publishes the latest frame here. A write goes straight to
    the UI-owned slot once it is attached; until then the writer records the
    announced format so the engine can ship a ``PreviewFormatChanged`` event and
    later attach the slot the UI created (``attach_slot``). The format/announce/
    slot-ready state machine lives here, so a fake transport can drive it in
    tests without a child process or real shared memory.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._slots: dict[PreviewSlotKey, AttachedPreviewSlot] = {}
        self._pending: dict[PreviewSlotKey, tuple[int, int, int]] = {}
        self._announced: dict[PreviewSlotKey, tuple[int, int, int, int]] = {}
        self._format_generations: dict[PreviewSlotKey, int] = {}
        self._slot_ready: dict[PreviewSlotKey, bool] = {}
        self._latest_preview: dict[PreviewSlotKey, ImagePreview] = {}

    def publish_image(self, owner_id: UUID, source_port_id: str, preview: ImagePreview) -> None:
        key = (owner_id, source_port_id)
        dimensions = (preview.width, preview.height, preview.channels)
        with self._lock:
            slot = self._slots.get(key)
            if (
                slot is not None
                and (
                    slot.descriptor.width,
                    slot.descriptor.height,
                    slot.descriptor.channels,
                )
                == dimensions
            ):
                try:
                    slot.write(preview)
                except (BufferError, OSError, RuntimeError, ValueError):
                    slot.close()
                    self._slots.pop(key, None)
                else:
                    self._latest_preview[key] = preview
                    if self._slot_ready.get(key) is False:
                        self._slot_ready[key] = True
                    return
            # Retain the latest frame so a slot attached after publication (e.g.
            # a short source that ends mid-handshake) still receives a frame.
            self._latest_preview[key] = preview
            current = self._announced.get(key)
            if current is None or current[1:] != dimensions:
                self._pending[key] = dimensions

    def take_pending_announcements(self) -> list[tuple[PreviewSlotKey, int, tuple[int, int, int]]]:
        """Drain keys needing (re-)announcement; return ``(key, generation, dims)``.

        Assigns a fresh format generation when the dimensions changed (closing
        any stale slot) and reuses the existing generation otherwise, mirroring
        the announce-once-per-format policy.
        """

        with self._lock:
            items: list[tuple[PreviewSlotKey, int, tuple[int, int, int]]] = []
            for key in sorted(self._pending, key=lambda item: (str(item[0]), item[1])):
                dimensions = self._pending.pop(key)
                current = self._announced.get(key)
                if current is None or current[1:] != dimensions:
                    generation = self._format_generations.get(key, 0) + 1
                    self._format_generations[key] = generation
                    self._announced[key] = (generation, *dimensions)
                    previous = self._slots.pop(key, None)
                    if previous is not None:
                        previous.close()
                    self._slot_ready[key] = False
                else:
                    generation = current[0]
                items.append((key, generation, dimensions))
            return items

    def attach_slot(self, descriptor: PreviewSlotDescriptor) -> None:
        key = (descriptor.owner_id, descriptor.source_port_id)
        replacement = AttachedPreviewSlot(descriptor)
        with self._lock:
            announced = self._announced.get(key)
            if announced != (
                descriptor.generation,
                descriptor.width,
                descriptor.height,
                descriptor.channels,
            ):
                replacement.close()
                raise ValueError("Preview slot does not match the announced format")
            previous = self._slots.pop(key, None)
            self._slots[key] = replacement
            # Flush a frame retained before the slot attached (e.g. a short
            # source that ended mid-handshake) so the UI has data immediately.
            retained = self._latest_preview.get(key)
            if retained is not None and (
                retained.width,
                retained.height,
                retained.channels,
            ) == (descriptor.width, descriptor.height, descriptor.channels):
                try:
                    replacement.write(retained)
                except (BufferError, OSError, RuntimeError, ValueError):
                    pass
                else:
                    self._slot_ready[key] = True
        if previous is not None:
            previous.close()

    def pending_handshake(self) -> bool:
        """True while any announced slot has not yet written its first frame."""

        with self._lock:
            return any(not ready for ready in self._slot_ready.values())

    def poll_images(
        self, after_sequences: Mapping[PreviewSlotKey, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        # The child face never polls; the UI reads via SharedMemoryPreviewReader.
        return ()

    def peek_sequences(self) -> Mapping[PreviewSlotKey, int]:
        return {}

    def clear(self) -> None:
        with self._lock:
            for slot in self._slots.values():
                slot.close()
            self._slots.clear()
            self._pending.clear()
            self._announced.clear()
            self._format_generations.clear()
            self._slot_ready.clear()
            self._latest_preview.clear()

    def close(self) -> None:
        self.clear()


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


__all__ = [
    "AttachedPreviewSlot",
    "InMemoryPreviewTransport",
    "OwnedPreviewSlot",
    "PreviewSlotKey",
    "PreviewTransport",
    "SharedMemoryPreviewReader",
    "SharedMemoryPreviewWriter",
    "preview_slot_size",
]
