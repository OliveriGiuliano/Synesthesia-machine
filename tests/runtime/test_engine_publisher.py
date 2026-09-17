"""Event-driven engine preview publisher loop tests.

The publisher used to wake at a blind 60 Hz and re-poll three sorted preview
maps on every wake, even while paused. It now waits on a dirty signal set by
the preview broker and only keeps a slow safety poll while announced shared
slots are still completing their parent/child handshake.
"""

from __future__ import annotations

import threading
import time
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    ImagePreview,
    NoteActivity,
    NotePreview,
    freeze_uint8_preview,
)
from synesthesia_machine.runtime.engine_server import _EventPublisher
from synesthesia_machine.runtime.preview_channel import SharedMemoryPreviewWriter

OWNER_ID = UUID("00000000-0000-0000-0000-0000000000e0")


class _CollectingQueue:
    """EventQueueWriter stub that records every published event."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def put_nowait(self, obj: object) -> None:
        self.events.append(obj)

    def close(self) -> None:
        return None


class _StubEngine:
    """Engine stand-in: next_* return nothing until armed, then each stored note once."""

    def __init__(self) -> None:
        self.poll_calls = 0
        self.notes: list[object] = []
        self._delivered: set[object] = set()

    def next_image_previews(self) -> tuple[()]:
        return ()

    def next_note_previews(self) -> tuple:
        self.poll_calls += 1
        fresh = tuple(n for n in self.notes if n not in self._delivered)
        if not fresh:
            return ()
        self._delivered.update(fresh)
        return fresh

    def next_value_previews(self) -> tuple[()]:
        return ()


def _make_publisher(
    engine: _StubEngine, queue: _CollectingQueue, heartbeat_interval_s: float = 0.25
) -> tuple[_EventPublisher, _StubEngine, _CollectingQueue, threading.Event]:
    wake = threading.Event()
    publisher = _EventPublisher(
        engine,  # type: ignore[arg-type]
        queue,
        writer=SharedMemoryPreviewWriter(),
        started_monotonic_ns=0,
        heartbeat_interval_s=heartbeat_interval_s,
        preview_wake=wake,
    )
    return publisher, engine, queue, wake


def test_publisher_stays_quiet_without_new_previews() -> None:
    publisher, engine, queue, wake = _make_publisher(_StubEngine(), _CollectingQueue())
    publisher.graph_activated(1)
    publisher.start()
    try:
        time.sleep(0.35)  # several 60 Hz slots under the old design
        wake.set()  # dirty signal with nothing stored: one poll, no event
    finally:
        publisher.close()
    assert engine.poll_calls == 1
    heartbeat_events = [event for event in queue.events if type(event).__name__ == "Heartbeat"]
    assert len(heartbeat_events) >= 1
    note_events = [
        event for event in queue.events if type(event).__name__ == "NotePreviewsPublished"
    ]
    assert note_events == []


def test_publisher_publishes_promptly_on_dirty_signal() -> None:
    engine = _StubEngine()
    engine.notes.append(NotePreview(OWNER_ID, 1, 3, (NoteActivity(0, 60, 100),)))
    publisher, engine, queue, wake = _make_publisher(engine, _CollectingQueue())
    publisher.graph_activated(1)
    publisher.start()
    try:
        started = time.monotonic()
        wake.set()
        deadline = started + 2.0
        while time.monotonic() < deadline:
            if any(type(event).__name__ == "NotePreviewsPublished" for event in queue.events):
                break
            time.sleep(0.005)
        elapsed = time.monotonic() - started
        published = [
            event for event in queue.events if type(event).__name__ == "NotePreviewsPublished"
        ]
        assert published, "no NotePreviewsPublished event within 2 s"
        assert elapsed < 0.1, f"dirty signal took {elapsed:.3f} s to publish"
    finally:
        publisher.close()
    assert engine.poll_calls >= 1


def test_publisher_handshake_poll_keeps_going_until_slots_settle() -> None:
    # Announced slots that never receive a frame keep the safety poll alive:
    # the publisher must still take a broker lock pass well past one 60 Hz
    # slot, unlike a pure event-driven design (which would stall the
    # announce/configure handshake after the last preview).
    publisher, engine, _, _ = _make_publisher(_StubEngine(), _CollectingQueue())
    publisher.graph_activated(1)
    # Drive the writer's public surface to announce a slot that never receives
    # a first frame: publishing records the pending format, and draining it
    # marks the slot announced-but-not-ready, keeping the safety poll alive.
    publisher._writer.publish_image(  # pyright: ignore[reportPrivateUsage]
        OWNER_ID,
        "image",
        ImagePreview(
            OWNER_ID,
            "image",
            1,
            1,
            2,
            2,
            3,
            freeze_uint8_preview(np.full((2, 2, 3), 1, dtype=np.uint8)),
        ),
    )
    publisher._writer.take_pending_announcements()  # pyright: ignore[reportPrivateUsage]
    publisher.start()
    try:
        time.sleep(0.35)
        assert engine.poll_calls >= 2  # safety polls kept the handshake alive
    finally:
        publisher.close()


def test_publisher_activation_and_shutdown_do_not_clear_writer_state() -> None:
    # The broker's apply is the single activation clear site: the publisher
    # only records the revision (and stops its loop at shutdown), so an
    # announced-but-unresolved slot survives a revision change and keeps the
    # handshake safety poll alive.
    publisher, engine, _, _ = _make_publisher(_StubEngine(), _CollectingQueue())
    writer = publisher._writer  # pyright: ignore[reportPrivateUsage]
    writer.publish_image(  # pyright: ignore[reportPrivateUsage]
        OWNER_ID,
        "image",
        ImagePreview(
            OWNER_ID,
            "image",
            1,
            1,
            2,
            2,
            3,
            freeze_uint8_preview(np.full((2, 2, 3), 1, dtype=np.uint8)),
        ),
    )
    writer.take_pending_announcements()  # pyright: ignore[reportPrivateUsage]
    assert writer.pending_handshake()

    publisher.graph_activated(2)
    assert writer.pending_handshake()

    publisher.start()
    try:
        time.sleep(0.3)
        assert engine.poll_calls >= 2
    finally:
        publisher.close()
    assert writer.pending_handshake()
