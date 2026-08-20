"""Qt-free, latest-value preview publication behind the final EngineClient API."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeGuard
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
    PortType,
    ValuePreview,
)
from synesthesia_machine.media import image_to_display_uint8
from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
from synesthesia_machine.runtime.execution_plan import CompiledNode, ExecutionPlan, PortKey
from synesthesia_machine.runtime.scheduler import TickResult

type MonotonicClock = Callable[[], float]
type ImagePreviewConverter = Callable[[ImageFrame, int], NDArray[np.uint8]]
type ChannelPreviewConverter = Callable[[ChannelFrame, int], NDArray[np.uint8]]

_NOTE_PREVIEW_FPS = 60
# Link pills are anchored on the producer, so preview cadence and dimensions are
# application policies rather than parameters read from a downstream display node.
# The larger dock stays at its historical cadence while pill-only work is cheaper.
_IMAGE_DOCK_PREVIEW_FPS = 30
_IMAGE_PILL_PREVIEW_FPS = 15
_IMAGE_PREVIEW_MAX_DIMENSION = 800


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
class _ValueTarget:
    owner_id: UUID
    source: PortKey
    port_type: str
    interval_s: float = 1.0 / _NOTE_PREVIEW_FPS


@dataclass(frozen=True, slots=True)
class _PreviewConfiguration:
    image_targets: tuple[_ImageTarget, ...]
    note_targets: tuple[_NoteTarget, ...]
    value_targets: tuple[_ValueTarget, ...]


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
        self._image_previews: dict[tuple[UUID, str], ImagePreview] = {}
        self._note_previews: dict[UUID, NotePreview] = {}
        self._image_sequences: dict[tuple[UUID, str], int] = {}
        self._note_sequences: dict[UUID, int] = {}
        self._image_last_published: dict[tuple[UUID, str], float] = {}
        self._note_last_published: dict[UUID, float] = {}
        self._value_targets: tuple[_ValueTarget, ...] = ()
        self._value_previews: dict[tuple[UUID, str], ValuePreview] = {}
        self._value_sequences: dict[tuple[UUID, str], int] = {}
        self._value_last_published: dict[tuple[UUID, str], float] = {}
        self._image_publication_times: deque[float] = deque()
        self._generation = 0

    def configure(self, plan: ExecutionPlan) -> None:
        """Replace targets and clear all payloads from the previous graph activation."""

        self.apply(self.prepare(plan))

    @staticmethod
    def prepare(plan: ExecutionPlan) -> _PreviewConfiguration:
        """Validate and materialize preview targets before a plan is committed.

        Image, channel, and scalar value previews are anchored on the producing
        node rather than a downstream display node, so a link pill can show live
        data on every connection regardless of its destination. Every connected
        image or channel output, and every connected INT/FLOAT scalar output,
        gets its own target so each pill is routed to the exact producing port.
        Note previews remain anchored on the note visualizer.
        """

        connected_sources: set[PortKey] = set()
        dock_sources: set[PortKey] = set()
        for node in plan.nodes:
            for binding in node.input_bindings.values():
                connected_sources.add(binding.source)
            if node.definition.type_id in {
                DISPLAY_IMAGE_DATA_TYPE_ID,
                CHANNEL_DISPLAY_TYPE_ID,
            }:
                dock_sources.update(binding.source for binding in node.input_bindings.values())

        image_targets: list[_ImageTarget] = []
        note_targets: list[_NoteTarget] = []
        value_targets: list[_ValueTarget] = []
        for node in plan.nodes:
            if not node.is_demanded:
                continue
            if node.definition.type_id == NOTE_VISUALIZER_TYPE_ID:
                binding = node.input_bindings.get("midi")
                if binding is not None:
                    note_targets.append(_NoteTarget(node.node_id, binding.source))
            image_targets.extend(_connected_image_targets(node, connected_sources, dock_sources))
            value_targets.extend(_connected_scalar_targets(node, connected_sources))

        return _PreviewConfiguration(
            tuple(sorted(image_targets, key=lambda item: (str(item.node_id), item.source.port_id))),
            tuple(sorted(note_targets, key=lambda item: str(item.node_id))),
            tuple(
                sorted(
                    value_targets,
                    key=lambda item: (str(item.owner_id), item.source.port_id),
                )
            ),
        )

    def apply(self, configuration: _PreviewConfiguration) -> None:
        """Atomically install already-prepared targets at the safe swap boundary."""

        with self._lock:
            self._generation += 1
            self._image_targets = configuration.image_targets
            self._note_targets = configuration.note_targets
            self._value_targets = configuration.value_targets
            self._clear_locked()

    def clear(self) -> None:
        """Forget current targets and previews, preventing stale cross-activation data."""

        with self._lock:
            self._generation += 1
            self._image_targets = ()
            self._note_targets = ()
            self._value_targets = ()
            self._clear_locked()

    @property
    def generation(self) -> int:
        """Return the active target generation for bounded asynchronous publication."""

        with self._lock:
            return self._generation

    def publish(self, result: TickResult, *, expected_generation: int | None = None) -> None:
        """Coalesce one completed tick into each due preview target."""

        now = self._monotonic()
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return
            generation = self._generation
            image_targets = tuple(
                target
                for target in self._image_targets
                if _is_due(
                    self._image_last_published.get((target.node_id, target.source.port_id)),
                    now,
                    target.interval_s,
                )
            )
            note_targets = tuple(
                target
                for target in self._note_targets
                if _is_due(self._note_last_published.get(target.node_id), now, target.interval_s)
            )
            value_targets = tuple(
                target
                for target in self._value_targets
                if _is_due(
                    self._value_last_published.get((target.owner_id, target.source.port_id)),
                    now,
                    target.interval_s,
                )
            )

        image_publications: list[
            tuple[_ImageTarget, ImageFrame | ChannelFrame, NDArray[np.uint8]]
        ] = []
        converted: dict[
            tuple[int, int, str], tuple[ImageFrame | ChannelFrame, NDArray[np.uint8]]
        ] = {}
        for target in image_targets:
            value = result.values.get(target.source)
            if not isinstance(value, (ImageFrame, ChannelFrame)):
                continue
            cache_key = (id(value), target.max_dimension, target.source_kind)
            cached = converted.get(cache_key)
            if cached is not None and cached[0] is value:
                image_publications.append((target, value, cached[1]))
                continue
            if target.source_kind == "IMAGE" and isinstance(value, ImageFrame):
                data = self._image_converter(value, target.max_dimension)
                converted[cache_key] = (value, data)
                image_publications.append((target, value, data))
            elif target.source_kind == "CHANNEL" and isinstance(value, ChannelFrame):
                data = self._channel_converter(value, target.max_dimension)
                converted[cache_key] = (value, data)
                image_publications.append((target, value, data))

        note_publications: list[tuple[_NoteTarget, MidiStateFrame, tuple[NoteActivity, ...]]] = []
        for target in note_targets:
            value = result.values.get(target.source)
            if isinstance(value, MidiStateFrame):
                notes = tuple(
                    NoteActivity(key.channel, key.note, velocity)
                    for key, velocity in sorted(value.notes.items())
                )
                note_publications.append((target, value, notes))

        value_tick_index = _result_tick_index(result) if value_targets else None

        with self._lock:
            if generation != self._generation:
                return
            for target, value, data in image_publications:
                key = (target.node_id, target.source.port_id)
                if not _is_due(self._image_last_published.get(key), now, target.interval_s):
                    continue
                sequence = self._image_sequences.get(key, 0) + 1
                height, width, channels = data.shape
                self._image_previews[key] = ImagePreview(
                    target.node_id,
                    target.source.port_id,
                    sequence,
                    value.context.tick_index,
                    width,
                    height,
                    channels,
                    data,
                )
                self._image_sequences[key] = sequence
                self._image_last_published[key] = now
                self._image_publication_times.append(now)
            self._trim_image_publication_times_locked(now)
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
            # Value capture keys off the source tick index: a scalar pill only
            # refreshes when a source (frame) tick supplies a tick index. This is
            # an intentional, tested invariant, not an accident of the current
            # media sources (which all emit frames).
            if value_tick_index is not None:
                for target in value_targets:
                    key = (target.owner_id, target.source.port_id)
                    if not _is_due(
                        self._value_last_published.get(key),
                        now,
                        target.interval_s,
                    ):
                        continue
                    value = result.values.get(target.source)
                    if not _is_scalar(value):
                        continue
                    sequence = self._value_sequences.get(key, 0) + 1
                    self._value_previews[key] = ValuePreview(
                        owner_id=target.owner_id,
                        source_port_id=target.source.port_id,
                        sequence=sequence,
                        tick_index=value_tick_index,
                        port_type=target.port_type,
                        text=_format_scalar(value),
                    )
                    self._value_sequences[key] = sequence
                    self._value_last_published[key] = now

    def poll_images(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        with self._lock:
            return tuple(
                preview
                for key, preview in sorted(
                    self._image_previews.items(),
                    key=lambda item: (str(item[0][0]), item[0][1]),
                )
                if preview.sequence > thresholds.get(key, 0)
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

    def poll_values(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        thresholds = after_sequences or {}
        with self._lock:
            return tuple(
                preview
                for key, preview in sorted(
                    self._value_previews.items(),
                    key=lambda item: (str(item[0][0]), item[0][1]),
                )
                if preview.sequence > thresholds.get(key, 0)
            )

    def preview_fps(self) -> float:
        """Return image/channel preview publications in the latest one-second window.

        Each connected image or channel output publishes on its own schedule, so the
        value scales with the number of connected image/channel outputs rather than
        with the number of display nodes.
        """

        now = self._monotonic()
        with self._lock:
            self._trim_image_publication_times_locked(now)
            return float(len(self._image_publication_times))

    def _trim_image_publication_times_locked(self, now: float) -> None:
        cutoff = now - 1.0
        while self._image_publication_times and self._image_publication_times[0] < cutoff:
            self._image_publication_times.popleft()

    def _clear_locked(self) -> None:
        self._image_previews.clear()
        self._note_previews.clear()
        self._value_previews.clear()
        self._image_sequences.clear()
        self._note_sequences.clear()
        self._value_sequences.clear()
        self._image_last_published.clear()
        self._note_last_published.clear()
        self._value_last_published.clear()
        self._image_publication_times.clear()


def _preview_image_data(image: ImageFrame, max_dimension: int) -> NDArray[np.uint8]:
    display = image_to_display_uint8(image)
    return _bounded_preview(display, max_dimension)


def _preview_channel_data(channel: ChannelFrame, max_dimension: int) -> NDArray[np.uint8]:
    minimum = np.float32(channel.nominal_min)
    denominator = np.float32(channel.nominal_max - channel.nominal_min)
    in_range, _ = cv2.checkRange(
        channel.data,
        quiet=True,
        minVal=float(channel.nominal_min),
        maxVal=math.nextafter(float(channel.nominal_max), math.inf),
    )
    if in_range:
        scale = 255.0 / float(denominator)
        gray = cv2.convertScaleAbs(channel.data, alpha=scale, beta=-float(minimum) * scale)
    else:
        normalized = np.array(channel.data, dtype=np.float32, order="C", copy=True)
        np.subtract(normalized, minimum, out=normalized)
        np.divide(normalized, denominator, out=normalized)
        np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0, copy=False)
        np.clip(normalized, np.float32(0.0), np.float32(1.0), out=normalized)
        gray = cv2.convertScaleAbs(normalized, alpha=255.0)
    display = np.asarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), dtype=np.uint8)
    return _bounded_preview(display, max_dimension)


def _bounded_preview(display: NDArray[np.uint8], max_dimension: int) -> NDArray[np.uint8]:
    height, width = display.shape[:2]
    largest = max(width, height)
    if largest <= max_dimension:
        if display.flags.c_contiguous and not display.flags.writeable:
            return display
        immutable = np.ascontiguousarray(display, dtype=np.uint8)
        immutable.flags.writeable = False
        return immutable
    scale = max_dimension / largest
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = cv2.resize(display, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    if resized.ndim == 2:
        resized = resized[..., None]
    immutable = np.ascontiguousarray(resized, dtype=np.uint8)
    immutable.flags.writeable = False
    return immutable


def _is_due(previous: float | None, now: float, interval_s: float) -> bool:
    return previous is None or now - previous + 1e-12 >= interval_s


def _connected_image_targets(
    node: CompiledNode,
    connected_sources: set[PortKey],
    dock_sources: set[PortKey],
) -> tuple[_ImageTarget, ...]:
    """Collect every connected IMAGE/CHANNEL output, ordered by port id.

    A producer may expose several connected image or channel outputs (for
    example the four channel outputs of a channel separator); each needs its
    own target so a pill is never stamped with another port's frame.
    """

    targets: list[_ImageTarget] = []
    for port_id in sorted(node.output_types):
        port_type = node.output_types[port_id]
        if port_type not in (PortType.IMAGE, PortType.CHANNEL):
            continue
        source = PortKey(node.node_id, port_id)
        if source in connected_sources:
            targets.append(
                _ImageTarget(
                    node.node_id,
                    source,
                    1.0
                    / (
                        _IMAGE_DOCK_PREVIEW_FPS
                        if source in dock_sources
                        else _IMAGE_PILL_PREVIEW_FPS
                    ),
                    _IMAGE_PREVIEW_MAX_DIMENSION,
                    "CHANNEL" if port_type is PortType.CHANNEL else "IMAGE",
                )
            )
    return tuple(targets)


def _connected_scalar_targets(
    node: CompiledNode, connected_sources: set[PortKey]
) -> tuple[_ValueTarget, ...]:
    """Collect every connected INT/FLOAT output, ordered by port id.

    A producer may expose several connected scalar outputs; each needs its own
    target so a pill is never stamped with another port's value.
    """

    targets: list[_ValueTarget] = []
    for port_id in sorted(node.output_types):
        port_type = node.output_types[port_id]
        if port_type not in (PortType.INT, PortType.FLOAT):
            continue
        source = PortKey(node.node_id, port_id)
        if source in connected_sources:
            targets.append(_ValueTarget(node.node_id, source, port_type.value))
    return tuple(targets)


def _is_scalar(value: object) -> TypeGuard[int | float | np.integer | np.floating]:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float, np.integer, np.floating))


def _format_scalar(value: int | float | np.integer | np.floating) -> str:
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    number = float(value)
    if not math.isfinite(number):
        # Display and conversion paths must not render raw "nan"/"inf" text.
        return "\u2014"
    return format(number, ".6g")


def _result_tick_index(result: TickResult) -> int | None:
    for value in result.values.values():
        if isinstance(value, (ImageFrame, ChannelFrame, MidiStateFrame)):
            return value.context.tick_index
    return None


__all__ = [
    "ChannelPreviewConverter",
    "ImagePreviewConverter",
    "MonotonicClock",
    "PreviewBroker",
]
