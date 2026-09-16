"""Headless PreviewRouter tests: cursor ownership and pill/dock routing.

The router is driven against a fake preview-channel transport (canned frames,
"latest frame wins" per producing port, threshold-filtered polls) — no child
engine process and no Qt window — so the routing decision is asserted
directly: which preview lands on which sink, that a repeat frame is not
re-routed, and how the dock-visibility flags gate the docks.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts.engine_client import (
    ImagePreview,
    NoteActivity,
    NotePreview,
    ValuePreview,
    freeze_uint8_preview,
)
from synesthesia_machine.ui.preview_router import PreviewRouter, PumpedPreviews

_IMAGE_PORT = "image"
_VALUE_PORT = "value"

# Feeds a display visualizer, so its frames also land on the image dock.
_VISUALIZER_SOURCE = UUID("a" * 32)
# Feeds nothing display-worthy, so its frames stay on the canvas pill only.
_OTHER_SOURCE = UUID("b" * 32)
_NOTE_NODE = UUID("c" * 32)

_IMAGE_DOCK_SOURCES = frozenset({(_VISUALIZER_SOURCE, _IMAGE_PORT)})


class _FakePreviewChannel:
    """Stands in for the preview channel's UI side (ticket 02).

    ``publish_*`` stores the latest frame per producing port ("latest frame
    wins", like the bounded transport); ``poll_*_*`` returns every stored
    frame newer than the per-port sequence threshold the consumer keeps.
    """

    def __init__(self) -> None:
        self._images: dict[tuple[UUID, str], ImagePreview] = {}
        self._values: dict[tuple[UUID, str], ValuePreview] = {}
        self._notes: dict[UUID, NotePreview] = {}
        self.image_polls = 0
        self.value_polls = 0
        self.note_polls = 0
        self.fail_next_poll = False

    # -- publish (what the engine side of the channel would do) ------------

    def publish_image(self, preview: ImagePreview) -> None:
        self._images[(preview.owner_id, preview.source_port_id)] = preview

    def publish_value(self, preview: ValuePreview) -> None:
        self._values[(preview.owner_id, preview.source_port_id)] = preview

    def publish_note(self, preview: NotePreview) -> None:
        self._notes[preview.owner_id] = preview

    # -- poll (the PreviewPoller surface the router consumes) --------------

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        self.image_polls += 1
        if self.fail_next_poll:
            self.fail_next_poll = False
            raise RuntimeError("engine transport closed")
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self._images.values()
            if preview.sequence > thresholds.get((preview.owner_id, preview.source_port_id), 0)
        )

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        self.value_polls += 1
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self._values.values()
            if preview.sequence > thresholds.get((preview.owner_id, preview.source_port_id), 0)
        )

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        self.note_polls += 1
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self._notes.values()
            if preview.sequence > thresholds.get(preview.owner_id, 0)
        )


def _image(source: UUID, sequence: int, port: str = _IMAGE_PORT) -> ImagePreview:
    data = freeze_uint8_preview(np.full((2, 3, 3), sequence, dtype=np.uint8))
    return ImagePreview(
        owner_id=source,
        source_port_id=port,
        sequence=sequence,
        tick_index=sequence,
        width=3,
        height=2,
        channels=3,
        data=data,
    )


def _value(source: UUID, sequence: int, text: str = "1.00") -> ValuePreview:
    return ValuePreview(
        owner_id=source,
        source_port_id=_VALUE_PORT,
        sequence=sequence,
        tick_index=sequence,
        port_type="FLOAT",
        text=text,
    )


def _note(sequence: int) -> NotePreview:
    return NotePreview(
        owner_id=_NOTE_NODE,
        sequence=sequence,
        tick_index=sequence,
        notes=(NoteActivity(channel=0, note=60, velocity=100),),
    )


def _image_keys(previews: tuple[ImagePreview, ...]) -> set[tuple[UUID, str, int]]:
    """Compare frames by identity fields (ImagePreview equality hits the ndarray)."""
    return {(p.owner_id, p.source_port_id, p.sequence) for p in previews}


# --- poll + cursor ownership ---------------------------------------------


def test_poll_advances_cursors_so_repeats_are_not_re_routed() -> None:
    channel = _FakePreviewChannel()
    channel.publish_image(_image(_VISUALIZER_SOURCE, 1))
    channel.publish_value(_value(_OTHER_SOURCE, 1))
    channel.publish_note(_note(1))
    router = PreviewRouter()

    first = router.poll(channel, note_visible=True)
    assert first is not None
    assert _image_keys(first.image_previews) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}
    assert [p.sequence for p in first.value_previews] == [1]
    assert [p.sequence for p in first.note_previews] == [1]
    assert router.image_sequences == {(_VISUALIZER_SOURCE, _IMAGE_PORT): 1}
    assert router.value_sequences == {(_OTHER_SOURCE, _VALUE_PORT): 1}
    assert router.note_sequences == {_NOTE_NODE: 1}

    # The cursors moved past every stored frame, so the identical next poll
    # routes nothing.
    second = router.poll(channel, note_visible=True)
    assert second is not None
    assert second.image_previews == ()
    assert second.value_previews == ()
    assert second.note_previews == ()

    # A new frame on one port re-opens only that port.
    channel.publish_image(_image(_VISUALIZER_SOURCE, 2))
    third = router.poll(channel, note_visible=True)
    assert third is not None
    assert _image_keys(third.image_previews) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 2)}
    assert third.value_previews == ()
    assert third.note_previews == ()


def test_hidden_note_dock_is_not_polled_and_reopens_on_show() -> None:
    channel = _FakePreviewChannel()
    channel.publish_note(_note(1))
    router = PreviewRouter()

    hidden = router.poll(channel, note_visible=False)
    assert hidden is not None
    assert hidden.note_previews == ()
    assert channel.note_polls == 0, "the note port must not be polled while hidden"
    assert router.note_sequences == {}

    visible = router.poll(channel, note_visible=True)
    assert visible is not None
    assert [p.owner_id for p in visible.note_previews] == [_NOTE_NODE]
    assert router.note_sequences == {_NOTE_NODE: 1}


def test_clearing_cursors_re_serves_the_latest_frames() -> None:
    channel = _FakePreviewChannel()
    channel.publish_image(_image(_VISUALIZER_SOURCE, 1))
    channel.publish_note(_note(1))
    router = PreviewRouter()
    first = router.poll(channel, note_visible=True)
    assert first is not None
    assert first.image_previews
    assert first.note_previews

    router.clear_cursors()
    again = router.poll(channel, note_visible=True)
    assert again is not None
    assert _image_keys(again.image_previews) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}, (
        "forgetting the cursors re-serves the latest frame"
    )
    assert [p.owner_id for p in again.note_previews] == [_NOTE_NODE]

    # Hiding a dock clears only that dock's cursors.
    router.clear_note_cursors()
    partial = router.poll(channel, note_visible=True)
    assert partial is not None
    assert partial.image_previews == (), "the image cursor must be retained"
    assert [p.owner_id for p in partial.note_previews] == [_NOTE_NODE]


def test_failed_poll_yields_nothing_and_keeps_cursors() -> None:
    channel = _FakePreviewChannel()
    channel.publish_image(_image(_VISUALIZER_SOURCE, 1))
    router = PreviewRouter()

    channel.fail_next_poll = True
    assert router.poll(channel, note_visible=True) is None
    assert router.image_sequences == {}

    pumped = router.poll(channel, note_visible=True)
    assert pumped is not None
    assert _image_keys(pumped.image_previews) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}


# --- routing decision ------------------------------------------------------


def test_image_preview_goes_to_pill_and_dock_when_it_feeds_a_visualizer() -> None:
    router = PreviewRouter()
    pumped = PumpedPreviews(
        image_previews=(_image(_VISUALIZER_SOURCE, 1), _image(_OTHER_SOURCE, 1)),
        value_previews=(_value(_OTHER_SOURCE, 1),),
        note_previews=(_note(1),),
    )

    routed = router.route(pumped, image_visible=True, image_dock_sources=_IMAGE_DOCK_SOURCES)

    # Every image preview lands on the canvas link pill ...
    assert _image_keys(routed.image_pill) == {
        (_VISUALIZER_SOURCE, _IMAGE_PORT, 1),
        (_OTHER_SOURCE, _IMAGE_PORT, 1),
    }
    # ... and only the source feeding a display visualizer also lands on the dock.
    assert _image_keys(routed.image_dock) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}
    assert [p.sequence for p in routed.value_pill] == [1]
    assert [p.owner_id for p in routed.note_dock] == [_NOTE_NODE]


def test_image_dock_routing_is_gated_on_dock_visibility() -> None:
    router = PreviewRouter()
    pumped = PumpedPreviews(
        image_previews=(_image(_VISUALIZER_SOURCE, 1),),
        value_previews=(),
        note_previews=(),
    )

    hidden = router.route(pumped, image_visible=False, image_dock_sources=_IMAGE_DOCK_SOURCES)
    assert hidden.image_dock == ()
    assert _image_keys(hidden.image_pill) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}

    visible = router.route(pumped, image_visible=True, image_dock_sources=_IMAGE_DOCK_SOURCES)
    assert _image_keys(visible.image_dock) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}

    # With no graph source feeding a visualizer, even a visible dock gets nothing.
    no_sources = router.route(pumped, image_visible=True, image_dock_sources=frozenset())
    assert no_sources.image_dock == ()
    assert _image_keys(no_sources.image_pill) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}


def test_poll_then_route_mirrors_the_window_flow() -> None:
    channel = _FakePreviewChannel()
    channel.publish_image(_image(_VISUALIZER_SOURCE, 1))
    channel.publish_image(_image(_OTHER_SOURCE, 1))
    channel.publish_value(_value(_OTHER_SOURCE, 1))
    channel.publish_note(_note(1))
    router = PreviewRouter()

    pumped = router.poll(channel, note_visible=True)
    assert pumped is not None
    routed = router.route(pumped, image_visible=True, image_dock_sources=_IMAGE_DOCK_SOURCES)
    assert _image_keys(routed.image_pill) == {
        (_VISUALIZER_SOURCE, _IMAGE_PORT, 1),
        (_OTHER_SOURCE, _IMAGE_PORT, 1),
    }
    assert _image_keys(routed.image_dock) == {(_VISUALIZER_SOURCE, _IMAGE_PORT, 1)}
    assert [p.sequence for p in routed.value_pill] == [1]
    assert [p.owner_id for p in routed.note_dock] == [_NOTE_NODE]

    # The cursors mean the next identical tick routes nothing.
    again = router.poll(channel, note_visible=True)
    assert again is not None
    idle = router.route(again, image_visible=True, image_dock_sources=_IMAGE_DOCK_SOURCES)
    assert idle.image_pill == ()
    assert idle.image_dock == ()
    assert idle.value_pill == ()
    assert idle.note_dock == ()
