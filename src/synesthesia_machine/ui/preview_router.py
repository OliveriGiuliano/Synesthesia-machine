"""Preview routing: owns the per-port sequence cursors and the pill/dock sinks.

The preview pump polls the engine's preview transport at display cadence. A
frame a port produces is routed exactly once — the first time its sequence is
seen — onto the canvas link pill, and (for the preview docks) onto the
self-contained preview pages.

:class:`PreviewRouter` is the single owner of that routing:

- It keeps the "already shown up to this sequence" cursor per producing port,
  so a repeated poll of an unchanged frame is not re-routed.
- :meth:`PreviewRouter.poll` pulls the new frames from any
  :class:`PreviewPoller` (the production engine client stands in for the
  preview channel; a test fake stands in for the transport) and advances the
  cursors for what it returned.
- :meth:`PreviewRouter.route` decides, for one pumped batch, which sink each
  preview lands in: the canvas pills always take every frame; the image dock
  additionally takes the previews whose source port feeds a display
  visualizer, while the dock is visible; the note dock takes every note
  preview (its poll is skipped while the dock is hidden).

The router is Qt-free: the routing decision is a pure function of the batch,
the dock-visibility flags, and the graph's image-visualizer sources, so it is
testable headlessly against a fake transport. The compositor applies the
:class:`RoutingResult` to the scene and panels.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    ImagePreview,
    NotePreview,
    ValuePreview,
)

__all__ = [
    "PreviewPoller",
    "PreviewRouter",
    "PumpedPreviews",
    "RoutingResult",
]


@dataclass(frozen=True, slots=True)
class PumpedPreviews:
    """One preview-pump result, ready for the compositor to render."""

    image_previews: tuple[ImagePreview, ...]
    value_previews: tuple[ValuePreview, ...]
    note_previews: tuple[NotePreview, ...]


class PreviewPoller(Protocol):
    """The slice of the engine client the preview pump consumes.

    The production client polls the preview channel (in-memory or shared
    memory); a test fake implements the same three polls over canned frames.
    """

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]: ...

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]: ...

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]: ...


@dataclass(frozen=True, slots=True)
class RoutingResult:
    """Where each pumped preview lands.

    Every image/value preview lands on a canvas link pill; the ``image_dock``
    and ``note_dock`` buckets are the previews that additionally land on the
    self-contained preview pages.
    """

    image_pill: tuple[ImagePreview, ...]
    image_dock: tuple[ImagePreview, ...]
    value_pill: tuple[ValuePreview, ...]
    note_dock: tuple[NotePreview, ...]


class PreviewRouter:
    """Owns the preview cursors and routes each pumped preview to its sinks."""

    def __init__(self) -> None:
        # Per-port "already shown up to this sequence" cursors. A preview is
        # routed only once: the first time its sequence is seen.
        self._image_sequences: dict[tuple[UUID, str], int] = {}
        self._value_sequences: dict[tuple[UUID, str], int] = {}
        self._note_sequences: dict[UUID, int] = {}

    @property
    def image_sequences(self) -> dict[tuple[UUID, str], int]:
        """Live image-preview cursors (shared with the compositor for test seams)."""
        return self._image_sequences

    @property
    def value_sequences(self) -> dict[tuple[UUID, str], int]:
        """Live value-preview cursors (shared with the compositor for test seams)."""
        return self._value_sequences

    @property
    def note_sequences(self) -> dict[UUID, int]:
        """Live note-preview cursors (shared with the compositor for test seams)."""
        return self._note_sequences

    def poll(
        self,
        client: PreviewPoller,
        *,
        note_visible: bool,
    ) -> PumpedPreviews | None:
        """Pull the new frames and advance the per-port cursors.

        Canvas previews (image, value) are polled unconditionally; the note
        port is skipped while its dock is hidden. Returns ``None`` when the
        poll fails (e.g. the engine transport closed) so the caller skips the
        tick without touching any published state.
        """
        try:
            image_previews = client.poll_image_previews(self._image_sequences)
            value_previews = client.poll_value_previews(self._value_sequences)
            note_previews = client.poll_note_previews(self._note_sequences) if note_visible else ()
        except (RuntimeError, TimeoutError):
            return None
        pumped = PumpedPreviews(
            image_previews=tuple(image_previews),
            value_previews=tuple(value_previews),
            note_previews=tuple(note_previews),
        )
        for preview in pumped.image_previews:
            self._image_sequences[(preview.owner_id, preview.source_port_id)] = preview.sequence
        for preview in pumped.value_previews:
            self._value_sequences[(preview.owner_id, preview.source_port_id)] = preview.sequence
        for preview in pumped.note_previews:
            self._note_sequences[preview.owner_id] = preview.sequence
        return pumped

    def route(
        self,
        pumped: PumpedPreviews,
        *,
        image_visible: bool,
        image_dock_sources: Set[tuple[UUID, str]],
    ) -> RoutingResult:
        """Decide the sink for every preview in the batch.

        The canvas pills always take every frame. The image dock takes a
        preview only while it is visible and the preview's source port feeds a
        display visualizer (``image_dock_sources``); the note dock takes every
        note preview (its poll is skipped while the dock is hidden).
        """
        image_dock: tuple[ImagePreview, ...] = ()
        if image_visible:
            image_dock = tuple(
                preview
                for preview in pumped.image_previews
                if (preview.owner_id, preview.source_port_id) in image_dock_sources
            )
        return RoutingResult(
            image_pill=pumped.image_previews,
            image_dock=image_dock,
            value_pill=pumped.value_previews,
            note_dock=pumped.note_previews,
        )

    def clear_image_cursors(self) -> None:
        """Drop the image cursors (the image dock was hidden)."""
        self._image_sequences.clear()

    def clear_note_cursors(self) -> None:
        """Drop the note cursors (the note dock was hidden)."""
        self._note_sequences.clear()

    def clear_cursors(self) -> None:
        """Forget every cursor so the next poll re-serves each port's frame."""
        self._image_sequences.clear()
        self._value_sequences.clear()
        self._note_sequences.clear()
