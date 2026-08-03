"""Qt-free, latest-value preview publication behind the final EngineClient API."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    ImageFrame,
    ImagePreview,
    MidiStateFrame,
    NoteActivity,
    NotePreview,
    freeze_uint8_preview,
)
from synesthesia_machine.media import image_to_display_uint8
from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey
from synesthesia_machine.runtime.scheduler import TickResult

type MonotonicClock = Callable[[], float]
type ImagePreviewConverter = Callable[[ImageFrame, int], NDArray[np.uint8]]
type ChannelPreviewConverter = Callable[[ChannelFrame, int], NDArray[np.uint8]]

_NOTE_PREVIEW_FPS = 60


@dataclass(frozen=True, slots=True)
class _ImageTarget:
    node_id: UUID
    source: PortKey
    interval_s: float
    max_dimension: int
    source_kind: str = "IMAGE"


@dataclass(frozen=True, slots=True)
class _NoteTarget:
    node_id: UUID
    source: PortKey
    interval_s: float = 1.0 / _NOTE_PREVIEW_FPS


@dataclass(frozen=True, slots=True)
class _PreviewConfiguration:
    image_targets: tuple[_ImageTarget, ...]
    note_targets: tuple[_NoteTarget, ...]


class PreviewBroker:
    """Publish immutable latest previews without waiting for UI consumption."""

    def __init__(
        self,
        *,
        monotonic: MonotonicClock = time.monotonic,
        image_converter: ImagePreviewConverter | None = None,
        channel_converter: ChannelPreviewConverter | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._image_converter = image_converter or _preview_image_data
        self._channel_converter = channel_converter or _preview_channel_data
        self._lock = threading.Lock()
        self._image_targets: tuple[_ImageTarget, ...] = ()
        self._note_targets: tuple[_NoteTarget, ...] = ()
        self._image_previews: dict[UUID, ImagePreview] = {}
        self._note_previews: dict[UUID, NotePreview] = {}
        self._image_sequences: dict[UUID, int] = {}
        self._note_sequences: dict[UUID, int] = {}
        self._image_last_published: dict[UUID, float] = {}
        self._note_last_published: dict[UUID, float] = {}
        self._generation = 0

    def configure(self, plan: ExecutionPlan) -> None:
        """Replace targets and clear all payloads from the previous graph activation."""

        self.apply(self.prepare(plan))

    @staticmethod
    def prepare(plan: ExecutionPlan) -> _PreviewConfiguration:
        """Validate and materialize preview targets before a plan is committed."""

        image_targets: list[_ImageTarget] = []
        note_targets: list[_NoteTarget] = []
        for node in plan.nodes:
            if not node.is_demanded:
                continue
            if node.definition.type_id == DISPLAY_IMAGE_DATA_TYPE_ID:
                binding = node.input_bindings.get("image")
                if binding is None:
                    continue
                image_targets.append(
                    _ImageTarget(
                        node.node_id,
                        binding.source,
                        1.0 / _integer_parameter(node.parameters, "preview_fps"),
                        _integer_parameter(node.parameters, "max_dimension"),
                    )
                )
            elif node.definition.type_id == CHANNEL_DISPLAY_TYPE_ID:
                binding = node.input_bindings.get("channel")
                if binding is None:
                    continue
                image_targets.append(
                    _ImageTarget(
                        node.node_id,
                        binding.source,
                        1.0 / _integer_parameter(node.parameters, "preview_fps"),
                        _integer_parameter(node.parameters, "max_dimension"),
                        "CHANNEL",
                    )
                )
            elif node.definition.type_id == NOTE_VISUALIZER_TYPE_ID:
                binding = node.input_bindings.get("midi")
                if binding is not None:
                    note_targets.append(_NoteTarget(node.node_id, binding.source))
        return _PreviewConfiguration(
            tuple(sorted(image_targets, key=lambda item: str(item.node_id))),
            tuple(sorted(note_targets, key=lambda item: str(item.node_id))),
        )

    def apply(self, configuration: _PreviewConfiguration) -> None:
        """Atomically install already-prepared targets at the safe swap boundary."""

        with self._lock:
            self._generation += 1
            self._image_targets = configuration.image_targets
            self._note_targets = configuration.note_targets
            self._clear_locked()

    def clear(self) -> None:
        """Forget current targets and previews, preventing stale cross-activation data."""

        with self._lock:
            self._generation += 1
            self._image_targets = ()
            self._note_targets = ()
            self._clear_locked()

    def publish(self, result: TickResult) -> None:
        """Coalesce one completed tick into each due preview target."""

        now = self._monotonic()
        with self._lock:
            generation = self._generation
            image_targets = tuple(
                target
                for target in self._image_targets
                if _is_due(self._image_last_published.get(target.node_id), now, target.interval_s)
            )
            note_targets = tuple(
                target
                for target in self._note_targets
                if _is_due(self._note_last_published.get(target.node_id), now, target.interval_s)
            )

        image_publications: list[
            tuple[_ImageTarget, ImageFrame | ChannelFrame, NDArray[np.uint8]]
        ] = []
        for target in image_targets:
            value = result.values.get(target.source)
            if target.source_kind == "IMAGE" and isinstance(value, ImageFrame):
                image_publications.append(
                    (target, value, self._image_converter(value, target.max_dimension))
                )
            elif target.source_kind == "CHANNEL" and isinstance(value, ChannelFrame):
                image_publications.append(
                    (target, value, self._channel_converter(value, target.max_dimension))
                )

        note_publications: list[tuple[_NoteTarget, MidiStateFrame, tuple[NoteActivity, ...]]] = []
        for target in note_targets:
            value = result.values.get(target.source)
            if isinstance(value, MidiStateFrame):
                notes = tuple(
                    NoteActivity(key.channel, key.note, velocity)
                    for key, velocity in sorted(value.notes.items())
                )
                note_publications.append((target, value, notes))

        with self._lock:
            if generation != self._generation:
                return
            for target, value, data in image_publications:
                if not _is_due(
                    self._image_last_published.get(target.node_id), now, target.interval_s
                ):
                    continue
                sequence = self._image_sequences.get(target.node_id, 0) + 1
                height, width, channels = data.shape
                self._image_previews[target.node_id] = ImagePreview(
                    target.node_id,
                    sequence,
                    value.context.tick_index,
                    width,
                    height,
                    channels,
                    data,
                )
                self._image_sequences[target.node_id] = sequence
                self._image_last_published[target.node_id] = now
            for target, value, notes in note_publications:
                if not _is_due(
                    self._note_last_published.get(target.node_id), now, target.interval_s
                ):
                    continue
                sequence = self._note_sequences.get(target.node_id, 0) + 1
                self._note_previews[target.node_id] = NotePreview(
                    target.node_id,
                    sequence,
                    value.context.tick_index,
                    notes,
                )
                self._note_sequences[target.node_id] = sequence
                self._note_last_published[target.node_id] = now

    def poll_images(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        with self._lock:
            return tuple(
                preview
                for node_id, preview in sorted(
                    self._image_previews.items(), key=lambda item: str(item[0])
                )
                if preview.sequence > thresholds.get(node_id, 0)
            )

    def poll_notes(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        thresholds = after_sequences or {}
        with self._lock:
            return tuple(
                preview
                for node_id, preview in sorted(
                    self._note_previews.items(), key=lambda item: str(item[0])
                )
                if preview.sequence > thresholds.get(node_id, 0)
            )

    def _clear_locked(self) -> None:
        self._image_previews.clear()
        self._note_previews.clear()
        self._image_sequences.clear()
        self._note_sequences.clear()
        self._image_last_published.clear()
        self._note_last_published.clear()


def _preview_image_data(image: ImageFrame, max_dimension: int) -> NDArray[np.uint8]:
    display = image_to_display_uint8(image)
    return _bounded_preview(display, max_dimension)


def _preview_channel_data(channel: ChannelFrame, max_dimension: int) -> NDArray[np.uint8]:
    denominator = np.float32(channel.nominal_max - channel.nominal_min)
    normalized = (channel.data - np.float32(channel.nominal_min)) / denominator
    safe = np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0)
    gray = np.rint(np.clip(safe, 0.0, 1.0) * np.float32(255.0)).astype(np.uint8)
    display = np.repeat(gray[..., None], 3, axis=2)
    return _bounded_preview(display, max_dimension)


def _bounded_preview(display: NDArray[np.uint8], max_dimension: int) -> NDArray[np.uint8]:
    height, width = display.shape[:2]
    largest = max(width, height)
    if largest <= max_dimension:
        return freeze_uint8_preview(display)
    scale = max_dimension / largest
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = cv2.resize(display, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    if resized.ndim == 2:
        resized = resized[..., None]
    return freeze_uint8_preview(np.asarray(resized, dtype=np.uint8))


def _integer_parameter(parameters: Mapping[str, object], parameter_id: str) -> int:
    value = parameters[parameter_id]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Expected integer parameter {parameter_id!r}")
    return value


def _is_due(previous: float | None, now: float, interval_s: float) -> bool:
    return previous is None or now - previous + 1e-12 >= interval_s


__all__ = [
    "ChannelPreviewConverter",
    "ImagePreviewConverter",
    "MonotonicClock",
    "PreviewBroker",
]
