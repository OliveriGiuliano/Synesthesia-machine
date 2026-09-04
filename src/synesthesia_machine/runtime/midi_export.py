"""Offline MIDI export: run a video-only graph end to end and capture every note.

The exporter drives a transient in-process engine (ADR-0016) whose video
sources run on a fast-forward playback clock: virtual time jumps to each
frame's presentation deadline, so the whole video is simulated at full speed
while every frame keeps its real PTS timestamp. A per-tick observer records
the desired MIDI state reaching each Send-MIDI node; consecutive states are
diffed into note-on/note-off events on the absolute video-time axis.

Callers must pass a registry whose output nodes are hardware-free (for
example ``create_builtin_registry(midi_output_service_factory=
NullMidiOutputService, synth_factory=NullDebugSynth)``) so the export never
opens a MIDI port, sends a note, or plays audio.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from synesthesia_machine.contracts import (
    DeviceCatalogue,
    MidiNoteKey,
    MidiStateFrame,
    NoData,
    SourceState,
)
from synesthesia_machine.graph.model import GraphSnapshot, NodeModel
from synesthesia_machine.media.video_source import (
    FrameCallback,
    PresentedSourceFrame,
    ResetCallback,
    VideoMetadata,
    VideoSourceService,
    inspect_video,
)
from synesthesia_machine.midi.smf import MidiExportEvent
from synesthesia_machine.nodes.base import ExecutionKind
from synesthesia_machine.nodes.input import LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID
from synesthesia_machine.nodes.output import SEND_MIDI_TYPE_ID
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey
from synesthesia_machine.runtime.in_process_engine import (
    InProcessEngineClient,
)
from synesthesia_machine.runtime.scheduler import TickResult

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.02
_EXPORT_TIMEOUT_S = 30 * 60.0


class MidiExportError(Exception):
    """Stable-code failure of an offline MIDI export."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class MidiExportProgress:
    """Frames processed so far; ``total`` is None when videos report no length."""

    processed: int
    total: int | None

    @property
    def fraction(self) -> float | None:
        if self.total is None or self.total < 1:
            return None
        return min(1.0, self.processed / self.total)


@dataclass(frozen=True, slots=True)
class MidiExportResult:
    """Events from start to end of the video, plus the simulated duration."""

    events: tuple[MidiExportEvent, ...]
    duration_s: float

    def to_standard_midi_file(self) -> bytes:
        from synesthesia_machine.midi.smf import encode_standard_midi_file

        return encode_standard_midi_file(self.events, self.duration_s)


class FastForwardPlaybackClock:
    """Playback clock that jumps virtual time to every frame's deadline.

    ``wait`` advances the virtual clock by the requested timeout instead of
    sleeping, so the video presentation loop runs at full decode speed while
    the PTS-driven timeline still stamps each frame with its true media time.
    An indefinite wait (pause) blocks briefly on the real clock until the
    wake event; exports never pause.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._now_ns = 0

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._now_ns

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool:
        if timeout_s is None:
            deadline = time.monotonic() + _POLL_INTERVAL_S
            while not wake_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.001)
            return wake_event.is_set()
        with self._lock:
            self._now_ns += max(0, round(timeout_s * 1_000_000_000))
        return wake_event.is_set()


class _ExportVideoSourceFactory:
    """VideoSourceService with a fast-forward clock injected per source."""

    def __call__(
        self,
        node_id: UUID,
        file_path: str | Path,
        *,
        process_every_nth_frame: int,
        playback_speed: float,
        loop: bool,
        stream_index: int,
        on_frame: object,
        on_reset: object,
    ) -> VideoSourceService:
        return VideoSourceService(
            node_id,
            file_path,
            process_every_nth_frame=process_every_nth_frame,
            playback_speed=playback_speed,
            loop=loop,
            stream_index=stream_index,
            on_frame=cast(FrameCallback, on_frame),
            on_reset=cast(ResetCallback | None, on_reset),
            clock=FastForwardPlaybackClock(),
        )


class _NullDeviceCatalogueService:
    """No hardware enumeration for the transient export engine."""

    def catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        del force_refresh
        return DeviceCatalogue()

    def close(self) -> None:
        pass


class _SourceFrameCount:
    __slots__ = ("every", "file_path", "node_id", "stream_index", "total")

    def __init__(self, node_id: UUID, file_path: str, stream_index: int, every: int) -> None:
        self.node_id = node_id
        self.file_path = file_path
        self.stream_index = stream_index
        self.every = every
        metadata = inspect_video(file_path, stream_index=stream_index)
        self.total = _processed_frame_count(metadata, every)


def _processed_frame_count(metadata: VideoMetadata, every: int) -> int | None:
    if metadata.frame_count is not None and metadata.frame_count > 0:
        return -(-metadata.frame_count // max(1, every))
    if metadata.duration_s is not None and metadata.average_rate:
        count = int(max(0.0, metadata.duration_s) * metadata.average_rate) // max(1, every)
        if count > 0:
            return count
    return None


def _int_parameter(node: NodeModel, parameter_id: str, default: int) -> int:
    """Read an integer node parameter with a tolerant fallback for odd values."""

    value = node.parameters.get(parameter_id, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _validate_export_inputs(
    snapshot: GraphSnapshot, *, registry: NodeRegistry
) -> tuple[list[_SourceFrameCount], list[UUID]]:
    """Return (video sources, send-MIDI node IDs) or raise a stable-coded error."""

    sources: list[_SourceFrameCount] = []
    send_ids: list[UUID] = []
    saw_source = False
    for node in snapshot.nodes:
        definition = registry.get(node.type_id)
        if definition is None:
            continue
        if definition.type_id == SEND_MIDI_TYPE_ID:
            send_ids.append(node.id)
            continue
        if definition.execution_kind is not ExecutionKind.SOURCE:
            continue
        saw_source = True
        if definition.type_id == LOAD_CAMERA_TYPE_ID:
            raise MidiExportError(
                "camera_source",
                "Exporting needs a finite timeline: camera sources cannot be exported.",
            )
        if definition.type_id != LOAD_VIDEO_TYPE_ID:
            raise MidiExportError(
                "unsupported_source",
                f"Source type {definition.type_id!r} cannot be exported.",
            )
        file_path = str(node.parameters.get("file_path", ""))
        if not file_path or not Path(file_path).is_file():
            raise MidiExportError(
                "missing_media",
                f"Load Video {definition.display_name} points to a file that cannot be read.",
            )
        if bool(node.parameters.get("loop", False)):
            # A looping source never reaches end-of-video, so there is no finite
            # "start to end of the video" to export.
            raise MidiExportError(
                "looping_source",
                "Exporting needs a finite timeline: a looping video source would repeat forever.",
            )
        sources.append(
            _SourceFrameCount(
                node.id,
                file_path,
                _int_parameter(node, "stream_index", 0),
                _int_parameter(node, "process_every_nth_frame", 1),
            )
        )
    if not saw_source:
        raise MidiExportError("no_source", "The graph has no source node to simulate.")
    if not send_ids:
        raise MidiExportError(
            "no_midi_output", "The graph has no Send MIDI node, so there is nothing to export."
        )
    return sources, send_ids


def run_midi_export(
    snapshot: GraphSnapshot,
    *,
    registry: NodeRegistry,
    progress: Callable[[MidiExportProgress], None] | None = None,
    stop_event: threading.Event | None = None,
) -> MidiExportResult:
    """Simulate the video-only graph from start to end and collect MIDI events.

    Runs a transient in-process engine; safe to call from a worker thread.
    Raises :class:`MidiExportError` with a stable ``code`` on failure.
    """

    sources, send_ids = _validate_export_inputs(snapshot, registry=registry)
    samples: dict[UUID, list[tuple[float, MidiStateFrame]]] = {node_id: [] for node_id in send_ids}
    sample_lock = threading.Lock()
    producer_keys: dict[UUID, PortKey | None] = {}
    resolved = False

    def _observe(
        source_node_id: UUID,
        frame: PresentedSourceFrame,
        result: TickResult,
        plan: ExecutionPlan,
    ) -> None:
        nonlocal resolved
        del source_node_id
        if not resolved:
            for node in plan.nodes:
                if node.definition.type_id != SEND_MIDI_TYPE_ID:
                    continue
                binding = node.input_bindings.get("midi")
                producer_keys[node.node_id] = binding.source if binding is not None else None
            resolved = True
        time_s = frame.context.source_time_s
        with sample_lock:
            for send_id, key in producer_keys.items():
                value = result.values.get(key) if key is not None else NoData
                if isinstance(value, MidiStateFrame):
                    state = value
                else:
                    # NoData (or an absent producer) means the output panicked:
                    # record an empty desired state so the diff closes notes.
                    state = MidiStateFrame(notes={}, context=frame.context, source_node_id=send_id)
                samples[send_id].append((time_s, state))

    client = InProcessEngineClient(
        registry,
        video_source_factory=_ExportVideoSourceFactory(),
        device_catalogue_service=_NullDeviceCatalogueService(),
        tick_observer=_observe,
    )
    started = time.monotonic()
    try:
        activation = client.activate(snapshot)
        if not activation.activated or not activation.report.is_valid:
            detail = (
                "; ".join(issue.message for issue in activation.report.issues[:3])
                or "the graph could not be activated"
            )
            raise MidiExportError("activation_failed", f"Export could not start: {detail}")
        client.play()
        totals = [source.total for source in sources]
        total = None if any(t is None for t in totals) else sum(t for t in totals if t is not None)
        while True:
            if stop_event is not None and stop_event.is_set():
                raise MidiExportError("cancelled", "The MIDI export was cancelled.")
            if time.monotonic() - started > _EXPORT_TIMEOUT_S:
                raise MidiExportError(
                    "timeout", "The MIDI export did not finish within 30 minutes."
                )
            statuses = client.source_status()
            by_id = {status.node_id: status for status in statuses}
            processed = 0
            finished = True
            for source in sources:
                status = by_id.get(source.node_id)
                if status is None:
                    raise MidiExportError(
                        "source_error", "A video source stopped reporting status."
                    )
                if status.state is SourceState.ERROR:
                    raise MidiExportError(
                        "source_error", status.last_error or "A video source failed."
                    )
                if status.state not in {SourceState.ENDED, SourceState.STOPPED}:
                    finished = False
                processed += status.processed_index
            if progress is not None:
                progress(MidiExportProgress(processed, total))
            if finished:
                break
            time.sleep(_POLL_INTERVAL_S)
        # Sources report ENDED the moment their last frame is published, while the
        # latest-frame worker may still be queueing or executing that final tick.
        # Drain it before collecting so the last frame's notes are never dropped.
        if not client.wait_until_idle(10.0):
            raise MidiExportError(
                "timeout", "The export finished but the engine was still processing frames."
            )

        with sample_lock:
            collected = {send_id: list(entries) for send_id, entries in samples.items()}
        duration_s = _export_duration(collected, sources)
        events = _diff_to_events(collected, duration_s)
        return MidiExportResult(tuple(events), duration_s)
    finally:
        client.close()


def _export_duration(
    collected: Mapping[UUID, list[tuple[float, MidiStateFrame]]],
    sources: Sequence[_SourceFrameCount],
) -> float:
    """End of the simulated timeline: the last captured event or the video end."""

    last_sample = max(
        (time_s for entries in collected.values() for time_s, _ in entries), default=0.0
    )
    video_end = 0.0
    for source in sources:
        metadata = inspect_video(source.file_path, stream_index=source.stream_index)
        if metadata.duration_s is not None:
            video_end = max(video_end, metadata.duration_s)
    return max(last_sample, video_end)


def _diff_to_events(
    collected: Mapping[UUID, list[tuple[float, MidiStateFrame]]],
    duration_s: float,
) -> list[MidiExportEvent]:
    """Diff consecutive desired states into note-on/off events on the time axis."""

    events: list[MidiExportEvent] = []
    for entries in collected.values():
        previous: dict[MidiNoteKey, int] = {}
        for time_s, state in sorted(entries, key=lambda item: item[0]):
            current: dict[MidiNoteKey, int] = {
                key: velocity for key, velocity in state.notes.items() if velocity > 0
            }
            for key, velocity in current.items():
                if key not in previous:
                    events.append(
                        MidiExportEvent(time_s, "note_on", key.channel, key.note, velocity)
                    )
            for key in previous:
                if key not in current:
                    events.append(MidiExportEvent(time_s, "note_off", key.channel, key.note, 0))
            previous = current
        # The source clock resets on end-of-video, panicking the MIDI outputs:
        # everything still sounding stops at the end of the timeline.
        for key in previous:
            events.append(MidiExportEvent(duration_s, "note_off", key.channel, key.note, 0))
    return events


__all__ = [
    "FastForwardPlaybackClock",
    "MidiExportError",
    "MidiExportProgress",
    "MidiExportResult",
    "run_midi_export",
]
