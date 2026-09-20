"""Offline MIDI export: run a video-only graph end to end and capture every note.

The exporter drives a transient in-process engine (ADR-0016) whose video
sources run on a fast-forward playback clock: virtual time jumps to each
frame's presentation deadline, so the whole video is simulated at full speed
while every frame keeps its real PTS timestamp. A per-tick observer records
the desired MIDI state reaching each MIDI output node (Send MIDI, Generate
Audio); consecutive states are diffed into note-on/note-off events on the
absolute video-time axis.

Reliability (ADR-0025): the observed stream is the product, so before
writing the exporter verifies that every processed source frame was observed
at every MIDI output node; a mismatch fails the export with
``observation_failed`` instead of truncating the file. Progress and the
simulated timeline end are read from the statuses the sources publish
(``total_index``, ``region_end_s``); the exporter no longer re-derives the
sources' frame arithmetic.

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
    SourceStatus,
)
from synesthesia_machine.graph.model import GraphSnapshot, NodeModel
from synesthesia_machine.media.source_config import VideoSourceConfig
from synesthesia_machine.media.video_source import (
    FrameCallback,
    PresentedSourceFrame,
    ResetCallback,
    VideoSourceService,
    inspect_video,
)
from synesthesia_machine.midi import diff_midi_states
from synesthesia_machine.midi.smf import MidiExportEvent
from synesthesia_machine.nodes import ExecutionKind, NodeRegistry
from synesthesia_machine.nodes.input import LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID
from synesthesia_machine.nodes.output import GENERATE_AUDIO_TYPE_ID, SEND_MIDI_TYPE_ID
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey
from synesthesia_machine.runtime.in_process_engine import (
    InProcessEngineClient,
)
from synesthesia_machine.runtime.scheduler import TickResult

_LOGGER = logging.getLogger(__name__)
_POLL_INTERVAL_S = 0.02
_EXPORT_TIMEOUT_S = 30 * 60.0
_MIDI_OUTPUT_TYPE_IDS = frozenset({SEND_MIDI_TYPE_ID, GENERATE_AUDIO_TYPE_ID})


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
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def wake_event(self) -> threading.Event:
        return self._wake_event

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._now_ns

    def wait(self, event: threading.Event, timeout_s: float | None) -> bool:
        if timeout_s is None:
            deadline = time.monotonic() + _POLL_INTERVAL_S
            while not event.is_set() and time.monotonic() < deadline:
                time.sleep(0.001)
            return event.is_set()
        with self._lock:
            self._now_ns += max(0, round(timeout_s * 1_000_000_000))
        return event.is_set()


class _ExportVideoSourceFactory:
    """VideoSourceService with a fast-forward clock injected per source."""

    def __call__(
        self,
        node_id: UUID,
        config: VideoSourceConfig,
        on_frame: object,
        on_reset: object,
    ) -> VideoSourceService:
        return VideoSourceService(
            node_id,
            config.file_path,
            process_every_nth_frame=config.process_every_nth_frame,
            playback_speed=config.playback_speed,
            # Export always simulates a single pass: the saved source's loop
            # setting is ignored so virtual time reaches the end of the
            # played segment and the source reports ENDED, ending the
            # simulation.
            loop=False,
            stream_index=config.stream_index,
            loop_start_s=config.loop_start_s,
            loop_end_s=config.loop_end_s,
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


def _int_parameter(node: NodeModel, parameter_id: str, default: int) -> int:
    """Read an integer node parameter with a tolerant fallback for odd values."""

    value = node.parameters.get(parameter_id, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _scan_export_nodes(
    snapshot: GraphSnapshot, *, registry: NodeRegistry
) -> tuple[list[UUID], list[UUID], bool, tuple[str, str] | None]:
    """One walk of the graph collecting the facts an export needs.

    Returns (video source node IDs, MIDI output node IDs, saw a source at
    all, first per-source problem in node order as a (code, message) pair).
    Both the eligibility check and the export-time validation consume these
    facts so the menu state and the exporter can never disagree about the
    rules.  The media probe is a readability gate only: the frame and
    region facts an export needs are published by the sources in their
    status (ADR-0025).
    """

    source_node_ids: list[UUID] = []
    midi_output_ids: list[UUID] = []
    saw_source = False
    first_problem: tuple[str, str] | None = None
    for node in snapshot.nodes:
        definition = registry.get(node.type_id)
        if definition is None:
            continue
        if definition.execution.type_id in _MIDI_OUTPUT_TYPE_IDS:
            midi_output_ids.append(node.id)
            continue
        if definition.execution.execution_kind is not ExecutionKind.SOURCE:
            continue
        saw_source = True
        if definition.execution.type_id == LOAD_CAMERA_TYPE_ID:
            first_problem = first_problem or (
                "camera_source",
                "Exporting needs a finite timeline: camera sources cannot be exported.",
            )
            continue
        if definition.execution.type_id != LOAD_VIDEO_TYPE_ID:
            first_problem = first_problem or (
                "unsupported_source",
                f"Source type {definition.execution.type_id!r} cannot be exported.",
            )
            continue
        file_path = str(node.parameters.get("file_path", ""))
        if not file_path or not Path(file_path).is_file():
            first_problem = first_problem or (
                "missing_media",
                f"Load Video {definition.presentation.display_name} "
                "points to a file that cannot be read.",
            )
            continue
        inspect_video(file_path, stream_index=_int_parameter(node, "stream_index", 0))
        source_node_ids.append(node.id)
    return source_node_ids, midi_output_ids, saw_source, first_problem


def midi_export_eligibility(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    *,
    graph_valid: bool = True,
) -> tuple[bool, str]:
    """Why the graph can (not) be exported to MIDI, as a stable reason code.

    Shares the rule set :func:`run_midi_export` enforces (same codes, same
    scan) plus the graph-valid precondition that only the editor knows.
    Returns ``(True, "")`` when the export can start.
    """

    _, midi_output_ids, saw_source, first_problem = _scan_export_nodes(snapshot, registry=registry)
    if not saw_source:
        return False, "no_source"
    if first_problem is not None:
        return False, first_problem[0]
    if not midi_output_ids:
        return False, "no_midi_output"
    if not graph_valid:
        return False, "invalid_graph"
    return True, ""


def _validate_export_inputs(
    snapshot: GraphSnapshot, *, registry: NodeRegistry
) -> tuple[list[UUID], list[UUID]]:
    """Return (video source node IDs, MIDI output node IDs) or raise a stable-coded error."""

    source_node_ids, midi_output_ids, saw_source, first_problem = _scan_export_nodes(
        snapshot, registry=registry
    )
    if first_problem is not None:
        raise MidiExportError(first_problem[0], first_problem[1])
    if not saw_source:
        raise MidiExportError("no_source", "The graph has no source node to simulate.")
    if not midi_output_ids:
        raise MidiExportError(
            "no_midi_output",
            "The graph has no MIDI output node (Send MIDI or Generate Audio) to export.",
        )
    return source_node_ids, midi_output_ids


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

    source_node_ids, midi_output_ids = _validate_export_inputs(snapshot, registry=registry)
    samples: dict[UUID, list[tuple[float, MidiStateFrame]]] = {
        node_id: [] for node_id in midi_output_ids
    }
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
                if node.definition.type_id not in _MIDI_OUTPUT_TYPE_IDS:
                    continue
                binding = node.input_bindings.get("midi")
                producer_keys[node.node_id] = binding.source if binding is not None else None
            resolved = True
        time_s = frame.context.source_time_s
        with sample_lock:
            for output_id, key in producer_keys.items():
                value = result.values.get(key) if key is not None else NoData
                if isinstance(value, MidiStateFrame):
                    state = value
                else:
                    # NoData (or an absent producer) means the output panicked:
                    # record an empty desired state so the diff closes notes.
                    state = MidiStateFrame(
                        notes={}, context=frame.context, source_node_id=output_id
                    )
                samples[output_id].append((time_s, state))

    client = InProcessEngineClient(
        registry,
        video_source_factory=_ExportVideoSourceFactory(),
        device_catalogue_service=_NullDeviceCatalogueService(),
        tick_observer=_observe,
        # The export consumes no previews: skip the preview worker and broker
        # so every tick stays on the pure graph path (ADR-0025).
        use_previews=False,
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
        final_statuses: tuple[SourceStatus, ...] = ()
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
            totals: list[int | None] = []
            finished = True
            for source_id in source_node_ids:
                status = by_id.get(source_id)
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
                totals.append(status.total_index)
            total = (
                None if any(t is None for t in totals) else sum(t for t in totals if t is not None)
            )
            if progress is not None:
                progress(MidiExportProgress(processed, total))
            if finished:
                final_statuses = statuses
                break
            time.sleep(_POLL_INTERVAL_S)
        # Sources report ENDED the moment their last frame is published, while the
        # latest-frame worker may still be queueing or executing that final tick.
        # Drain it before collecting so the last frame's notes are never dropped.
        if not client.wait_until_idle(10.0):
            raise MidiExportError(
                "timeout", "The export finished but the engine was still processing frames."
            )
        # Observation contract (ADR-0025): the engine keeps ticking through
        # observer failures (observation is diagnostic to the engine), so the
        # exporter proves completeness itself: every processed frame must
        # have been observed at every MIDI output, or the note stream is
        # incomplete.
        expected_ticks = sum(status.processed_index for status in final_statuses)
        for output_id in midi_output_ids:
            if len(samples[output_id]) != expected_ticks:
                raise MidiExportError(
                    "observation_failed",
                    "The export observed fewer MIDI states than the sources processed; "
                    "the note stream would be incomplete, so the export failed instead "
                    "of writing a truncated file.",
                )

        with sample_lock:
            collected = {send_id: list(entries) for send_id, entries in samples.items()}
        duration_s = _export_duration(collected, final_statuses)
        events = _diff_to_events(collected, duration_s)
        return MidiExportResult(tuple(events), duration_s)
    finally:
        client.close()


def _export_duration(
    collected: Mapping[UUID, list[tuple[float, MidiStateFrame]]],
    final_statuses: Sequence[SourceStatus],
) -> float:
    """End of the simulated timeline: the last captured event or the segment end."""

    last_sample = max(
        (time_s for entries in collected.values() for time_s, _ in entries), default=0.0
    )
    segment_end = max((status.region_end_s or 0.0 for status in final_statuses), default=0.0)
    return max(last_sample, segment_end)


def _diff_to_events(
    collected: Mapping[UUID, list[tuple[float, MidiStateFrame]]],
    duration_s: float,
) -> list[MidiExportEvent]:
    """Diff consecutive desired states into note-on/off events on the time axis."""

    events: list[MidiExportEvent] = []
    for entries in collected.values():
        previous: dict[MidiNoteKey, int] = {}
        for time_s, state in sorted(entries, key=lambda item: item[0]):
            # The export declares the on/off-only policy of ADR-0016/0019:
            # a zero-velocity desired note is off, so it is excluded before
            # the shared diff and velocity changes of held notes are
            # deliberately not projected to events.
            current: dict[MidiNoteKey, int] = {
                key: velocity for key, velocity in state.notes.items() if velocity > 0
            }
            diff = diff_midi_states(previous, current)
            for key, velocity in diff.added:
                events.append(MidiExportEvent(time_s, "note_on", key.channel, key.note, velocity))
            for key in diff.removed:
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
