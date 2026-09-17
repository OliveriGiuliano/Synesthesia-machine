"""Preview routing: decides which sink each pumped preview lands in.

The preview pump (the engine session's per-port sequence cursors) delivers
each new frame once; the router is the pure routing policy applied to one
pumped batch:

- The canvas link pills always take every frame.
- The image dock takes a preview only while it is visible and the preview's
  source port feeds a display visualizer (``image_dock_sources``).
- The note dock takes every note preview (its pump is skipped while the dock
  is hidden, so hidden batches carry no note previews).

The router is stateless and Qt-free: the routing decision is a pure function
of the batch and the dock-visibility flags, so it is testable headlessly and
a second consumer can reuse it without re-deriving the policy. The
compositor applies the :class:`RoutingResult` to the scene and panels.
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    ImagePreview,
    NotePreview,
    ValuePreview,
)

__all__ = [
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
    """Routes each pumped preview batch to its sinks (stateless policy)."""

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
        note preview (its pump is skipped while the dock is hidden).
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
