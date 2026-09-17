"""Headless PreviewRouter tests: the pill/dock routing decision.

The router is the stateless routing policy applied to one pumped batch;
per-port sequence cursors live in the engine session (covered by
``tests/runtime/test_engine_session.py``), so these tests build the pumped
batches directly.  No child engine process and no Qt window are involved.
"""

from __future__ import annotations

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
        notes=(NoteActivity(0, 60, 1),),
    )


def _image_keys(previews: tuple[ImagePreview, ...]) -> set[tuple[UUID, str, int]]:
    """Compare frames by identity fields (ImagePreview equality hits the ndarray)."""
    return {(p.owner_id, p.source_port_id, p.sequence) for p in previews}


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


def test_an_idle_tick_routes_nothing() -> None:
    router = PreviewRouter()

    idle = router.route(
        PumpedPreviews((), (), ()),
        image_visible=True,
        image_dock_sources=_IMAGE_DOCK_SOURCES,
    )

    assert idle.image_pill == ()
    assert idle.image_dock == ()
    assert idle.value_pill == ()
    assert idle.note_dock == ()
