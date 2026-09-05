"""Event-driven engine preview publisher loop tests.

The publisher used to wake at a blind 60 Hz and re-poll three sorted preview
maps on every wake, even while paused. It now waits on a dirty signal set by
the preview broker and only keeps a slow safety poll while announced shared
slots are still completing their parent/child handshake.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts import NoteActivity, NotePreview
from synesthesia_machine.runtime.engine_server import _EventPublisher

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
    """Engine stand-in: polls return nothing until armed, then one note preview."""

    def __init__(self) -> None:
        self.poll_calls = 0
        self.notes: list[object] = []

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[()]:
        return ()

    def poll_note_previews(self, after_sequences: Mapping[UUID, int] | None = None) -> tuple:
        self.poll_calls += 1
        if self.notes:
            return tuple(self.notes)
        return ()

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[()]:
        return ()


def _make_publisher(
    engine: _StubEngine, queue: _CollectingQueue, heartbeat_interval_s: float = 0.25
) -> tuple[_EventPublisher, _StubEngine, _CollectingQueue, threading.Event]:
    wake = threading.Event()
    publisher = _EventPublisher(
        engine,  # type: ignore[arg-type]
        queue,
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
    publisher.start()
    try:
        with publisher._lock:
            publisher._slot_ready[(OWNER_ID, "image")] = False
        time.sleep(0.35)
        assert engine.poll_calls >= 2  # safety polls kept the handshake alive
    finally:
        publisher.close()
