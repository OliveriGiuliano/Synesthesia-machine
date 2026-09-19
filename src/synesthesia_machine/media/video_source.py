"""Deterministic PyAV file playback with source-owned worker threads.

The service owns decode and presentation.  It publishes compact presented packets to
an engine-owned latest-frame mailbox, so graph execution can drop stale work without
changing the source's PTS-driven playback timeline.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from math import isfinite
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import av
import numpy as np

from synesthesia_machine.contracts import (
    ResetReason,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.media.region_pass import (
    DecodedVideoFrame,
    run_region_pass,
)
from synesthesia_machine.media.source_frame import (
    PresentedSourceFrame,
    build_presented_image_frame,
)
from synesthesia_machine.media_path import normalize_media_path

_NANOSECONDS_PER_SECOND = 1_000_000_000
_DEFAULT_DECODE_QUEUE_SIZE = 2
_DEFAULT_MAX_DECODE_FAILURES = 3


class PlaybackClock(Protocol):
    """Injectable monotonic clock and interruptible wait used by PTS pacing.

    The clock also owns the service's wake/stop synchronization events so
    tests can inject controllable primitives without touching service state.
    """

    @property
    def wake_event(self) -> threading.Event: ...

    @property
    def stop_event(self) -> threading.Event: ...

    def monotonic_ns(self) -> int: ...

    def wait(self, event: threading.Event, timeout_s: float | None) -> bool: ...


class SystemPlaybackClock:
    def __init__(self) -> None:
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def wake_event(self) -> threading.Event:
        return self._wake_event

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def monotonic_ns(self) -> int:
        return time.perf_counter_ns()

    def wait(self, event: threading.Event, timeout_s: float | None) -> bool:
        return event.wait(timeout_s)


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    path: Path
    stream_index: int
    codec_name: str
    width: int
    height: int
    duration_s: float | None
    average_rate: float | None
    frame_count: int | None


PresentedVideoFrame = PresentedSourceFrame


class PtsPlaybackTimeline:
    """Map media PTS values to one monotonic playback timeline."""

    def __init__(self, playback_speed: float = 1.0) -> None:
        if not isfinite(playback_speed) or playback_speed <= 0.0:
            raise ValueError("playback_speed must be finite and positive")
        self._playback_speed = playback_speed
        self._first_pts_s: float | None = None
        self._anchor_ns: int | None = None
        self._paused_at_ns: int | None = None

    def reset(self) -> None:
        self._first_pts_s = None
        self._anchor_ns = None
        self._paused_at_ns = None

    def target_ns(self, pts_seconds: float, now_ns: int) -> int:
        if not np.isfinite(pts_seconds):
            raise ValueError("frame PTS must be finite")
        if now_ns < 0:
            raise ValueError("monotonic time cannot be negative")
        if self._first_pts_s is None or self._anchor_ns is None:
            self._first_pts_s = pts_seconds
            self._anchor_ns = now_ns
        offset_ns = round(
            (pts_seconds - self._first_pts_s) * _NANOSECONDS_PER_SECOND / self._playback_speed
        )
        return self._anchor_ns + offset_ns

    def pause(self, now_ns: int) -> None:
        if self._paused_at_ns is None:
            self._paused_at_ns = now_ns

    def resume(self, now_ns: int) -> None:
        if self._paused_at_ns is None:
            return
        if self._anchor_ns is not None:
            self._anchor_ns += max(0, now_ns - self._paused_at_ns)
        self._paused_at_ns = None


class _QueueSignal(Enum):
    END = auto()
    LOOP = auto()


type _DecodeItem = DecodedVideoFrame | _QueueSignal
type FrameCallback = Callable[[PresentedSourceFrame], None]
type ResetCallback = Callable[[ResetReason], None]


def inspect_video(path: str | Path, *, stream_index: int = 0) -> VideoMetadata:
    """Read video metadata and close the PyAV container without starting playback."""

    source_path = normalize_media_path(path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Video file does not exist: {source_path}")
    if stream_index < 0:
        raise ValueError("stream_index cannot be negative")

    with av.open(str(source_path), mode="r") as container:
        video_streams = container.streams.video
        if stream_index >= len(video_streams):
            raise ValueError(
                f"Video stream index {stream_index} is unavailable; found {len(video_streams)}"
            )
        stream = video_streams[stream_index]
        duration_s: float | None = None
        if stream.duration is not None and stream.time_base is not None:
            duration_s = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            duration_s = float(container.duration) / float(av.time_base)
        average_rate = float(stream.average_rate) if stream.average_rate is not None else None
        frame_count = int(stream.frames) if stream.frames > 0 else None
        return VideoMetadata(
            path=source_path,
            stream_index=stream_index,
            codec_name=stream.codec_context.name or "unknown",
            width=int(stream.width),
            height=int(stream.height),
            duration_s=duration_s,
            average_rate=average_rate,
            frame_count=frame_count,
        )


def _describe_region_error(start_s: float, duration_s: float) -> str:
    """User-actionable message for a loop start at or past the video's end."""

    return (
        f"Start timestamp {start_s:.2f}s is at or past the end of the video "
        f"({duration_s:.2f}s), so the loop region is empty. Set the start "
        f"timestamp to a value before {duration_s:.2f}s."
    )


def _total_processed_index(
    metadata: VideoMetadata,
    *,
    every: int,
    region_start_s: float,
    region_end_s: float | None,
) -> int | None:
    """Presented frames the source will deliver over its region.

    ``None`` when the container reports neither a frame count nor a frame
    rate; ``0`` for a degenerate region.  A whole-file region whose duration
    is unknown counts every frame in the container.
    """
    if region_end_s is None:
        if metadata.frame_count is not None and metadata.frame_count > 0:
            return -(-metadata.frame_count // max(1, every))
        return None
    span = max(0.0, region_end_s - region_start_s)
    if span <= 0.0:
        return 0
    if metadata.frame_count is not None and metadata.frame_count > 0:
        if metadata.duration_s is None or metadata.duration_s <= 0.0:
            return -(-metadata.frame_count // max(1, every))
        region_frames = max(0.0, span / metadata.duration_s) * metadata.frame_count
        return max(0, int(region_frames) // max(1, every))
    if metadata.duration_s is not None and metadata.average_rate:
        count = int(max(0.0, span) * metadata.average_rate) // max(1, every)
        if count > 0:
            return count
    return None


class VideoSourceService:
    """Lifecycle owner for one compiled Load Video source node."""

    def __init__(
        self,
        node_id: UUID,
        file_path: str | Path,
        *,
        process_every_nth_frame: int = 1,
        playback_speed: float = 1.0,
        loop: bool = False,
        stream_index: int = 0,
        loop_start_s: float = 0.0,
        loop_end_s: float = 0.0,
        on_frame: FrameCallback,
        on_reset: ResetCallback | None = None,
        clock: PlaybackClock | None = None,
        decode_queue_size: int = _DEFAULT_DECODE_QUEUE_SIZE,
        max_decode_failures: int = _DEFAULT_MAX_DECODE_FAILURES,
    ) -> None:
        if process_every_nth_frame < 1:
            raise ValueError("process_every_nth_frame must be at least 1")
        if not isfinite(playback_speed) or playback_speed <= 0.0:
            raise ValueError("playback_speed must be finite and positive")
        for name, value in (("loop_start_s", loop_start_s), ("loop_end_s", loop_end_s)):
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be a finite timestamp at or after 0")
        if decode_queue_size < 1:
            raise ValueError("decode_queue_size must be at least 1")
        if max_decode_failures < 1:
            raise ValueError("max_decode_failures must be at least 1")

        self.node_id = node_id
        self._process_every_nth_frame = process_every_nth_frame
        self._loop = loop
        self._on_frame = on_frame
        self._on_reset = on_reset
        self._clock = clock or SystemPlaybackClock()
        self._max_decode_failures = max_decode_failures
        self._metadata = inspect_video(file_path, stream_index=stream_index)
        self._stream_index = self._metadata.stream_index
        # The played segment is [loop_start_s, loop_end_s); a zero end means
        # "until the end of the video". Timestamps beyond the video's duration
        # clamp to it, and an empty or inverted region falls back to
        # [0, loop_end_s) (or the whole file) so the source always has a
        # playable segment instead of failing to decode.
        duration_s = self._metadata.duration_s
        if loop_end_s > 0.0:
            region_end_s: float | None = loop_end_s
        elif duration_s is not None:
            region_end_s = duration_s
        else:
            region_end_s = None
        if duration_s is not None and region_end_s is not None:
            region_end_s = min(region_end_s, duration_s)
        region_start_s = loop_start_s
        if duration_s is not None:
            region_start_s = min(region_start_s, duration_s)
        if region_end_s is not None and region_start_s >= region_end_s:
            region_start_s = 0.0
            if region_end_s <= 0.0:
                region_end_s = None
        self._loop_start_s = loop_start_s
        self._region_error: str | None = None
        if duration_s is not None and loop_start_s >= duration_s:
            self._region_error = _describe_region_error(loop_start_s, duration_s)
        self._region_start_s = region_start_s
        self._region_end_s = region_end_s
        self._pass_start_s = region_start_s
        # ADR-0025: the presented-frame total of the active region,
        # published in the status so run-to-end consumers (the MIDI export)
        # read the source's own facts instead of re-deriving them.
        self._total_index = _total_processed_index(
            self._metadata,
            every=self._process_every_nth_frame,
            region_start_s=region_start_s,
            region_end_s=region_end_s,
        )
        # ADR-0024: a looping source keeps a bounded buffer of pre-decoded
        # loop-head frames so a pass boundary presents without stalling for
        # the restart cost. About one second of the region at the published
        # rate (reduced by the every-Nth selection), floored at 2 and capped
        # at the region's frame count; sources without a usable frame rate or
        # with a degenerate region do not pre-decode.
        head_budget: int | None = None
        average_rate = self._metadata.average_rate
        if loop and average_rate is not None and average_rate > 0.0 and region_end_s is not None:
            region_frames = int(
                max(0.0, region_end_s - region_start_s) * average_rate / process_every_nth_frame
            )
            if region_frames >= 1:
                head_budget = min(int(average_rate), max(2, region_frames))
        self._head_frame_budget = head_budget

        self._lock = threading.RLock()
        self._decode_queue: queue.Queue[_DecodeItem] = queue.Queue(decode_queue_size)
        # The injected clock owns the worker synchronization events; tests
        # gain controllable primitives without reaching into service state.
        self._stop_event = self._clock.stop_event
        self._wake_event = self._clock.wake_event
        self._timeline = PtsPlaybackTimeline(playback_speed)
        self._decode_thread: threading.Thread | None = None
        self._presentation_thread: threading.Thread | None = None
        # ADR-0024 loop head: the worker stages frames in a private queue and
        # swaps it in only while the presentation is not mid-consumption, so
        # at most two bounded head buffers exist at once.
        self._head_queue: queue.Queue[DecodedVideoFrame] = queue.Queue(head_budget or 0)
        self._head_thread: threading.Thread | None = None
        self._head_container: Any | None = None
        self._head_in_use = False
        self._head_trigger = threading.Event()
        # The live decode container stays open across passes of one instance
        # (ADR-0024); it is re-opened only when the file changes (reload) or
        # the source is closed/stopped.
        self._live_container: Any | None = None
        self._state = SourceState.ERROR if self._region_error is not None else SourceState.READY
        self._source_frame_index: int | None = None
        self._processed_index = 0
        self._skipped_by_selection = 0
        self._warnings = 0
        self._last_error = self._region_error
        self._position_s = 0.0
        self._seek_target_s = 0.0
        self._seek_event = threading.Event()
        self._seek_filter_pts: float | None = None
        self._seek_generation = 0
        self._applied_seek_generation = 0

    @property
    def metadata(self) -> VideoMetadata:
        return self._metadata

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        with self._lock:
            return SourceStatus(
                node_id=self.node_id,
                state=self._state,
                file_path=str(self._metadata.path),
                width=self._metadata.width,
                height=self._metadata.height,
                duration_s=self._metadata.duration_s,
                source_time_s=self._position_s,
                source_frame_index=self._source_frame_index,
                processed_index=self._processed_index,
                total_index=self._total_index,
                region_end_s=self._region_end_s,
                skipped_by_selection=self._skipped_by_selection,
                dropped_before_processing=dropped_before_processing,
                warnings=self._warnings,
                last_error=self._last_error,
            )

    def play(self) -> None:
        with self._lock:
            state = self._state
        if state is SourceState.CLOSED:
            raise RuntimeError("Video source is closed")
        if state is SourceState.ERROR:
            if self._region_error is None:
                raise RuntimeError("Video source must be reloaded after an error")
            # A configuration error (a start timestamp beyond the video's end)
            # cannot be cleared by playing or reloading the same configuration;
            # a corrected configuration replaces the source instead.
            return
        if state is SourceState.PLAYING:
            return
        if state is SourceState.PAUSED:
            self.resume()
            return
        self._start_pass(publish_restart=state is SourceState.ENDED)

    def _start_pass(self, *, publish_restart: bool) -> None:
        """Start (or restart) the decode/presentation workers for one pass.

        A pending seek makes the pass start at the seek target; otherwise the
        pass starts at the configured segment start.
        """
        if self._region_error is not None:
            # Re-assert the configuration error for any pass start (including
            # one after a stop) instead of starting workers for a region with
            # no frames.
            with self._lock:
                self._state = SourceState.ERROR
                self._last_error = self._region_error
            return
        # Stopping (and waking) first lets a paused worker leave its blocked
        # state wait, so the join below cannot time out; the fresh pass
        # re-arms both events.
        self._stop_event.set()
        self._wake_event.set()
        self._join_workers()
        self._clear_decode_queue()
        self._stop_event.clear()
        self._wake_event.clear()
        with self._lock:
            self._timeline.reset()
            self._processed_index = 0
            self._source_frame_index = None
            self._last_error = None
            if self._seek_event.is_set():
                # A seek issued while the source was not playing restarts the
                # pass at the requested position instead of the segment start.
                # A target at or beyond the region end cannot begin a pass
                # (no frame is presentable there): start at the segment start
                # instead, or every play would park at the end again.
                seek_target_s = self._seek_target_s
                self._seek_event.clear()
                if self._region_end_s is None or seek_target_s < self._region_end_s:
                    self._pass_start_s = seek_target_s
                else:
                    self._pass_start_s = self._region_start_s
                    self._seek_filter_pts = None
            else:
                self._pass_start_s = self._region_start_s
                # A fresh pass must not inherit the PTS filter of an earlier
                # seek: a seek that ended at the region end would otherwise
                # drop every frame of this pass.
                self._seek_filter_pts = None
            self._position_s = self._pass_start_s
            # The new presentation thread must not re-apply a seek that
            # _start_pass already honored as its pass start.
            self._applied_seek_generation = self._seek_generation
            self._state = SourceState.PLAYING
            self._presentation_thread = threading.Thread(
                target=self._presentation_loop,
                name=f"video-present-{self.node_id}",
                daemon=True,
            )
            self._decode_thread = threading.Thread(
                target=self._decode_loop,
                name=f"video-decode-{self.node_id}",
                daemon=True,
            )
            self._head_thread = None
            if self._head_frame_budget is not None:
                self._head_thread = threading.Thread(
                    target=self._head_loop,
                    name=f"video-head-{self.node_id}",
                    daemon=True,
                )
            presentation_thread = self._presentation_thread
            decode_thread = self._decode_thread
            head_thread = self._head_thread
        if publish_restart:
            self._publish_reset(ResetReason.SOURCE_RESTARTED)
        presentation_thread.start()
        decode_thread.start()
        if head_thread is not None:
            head_thread.start()
            # The first pass is the runway that warms the head before the
            # first loop boundary.
            self._head_trigger.set()

    def pause(self) -> None:
        with self._lock:
            if self._state is not SourceState.PLAYING:
                return
            self._timeline.pause(self._clock.monotonic_ns())
            self._state = SourceState.PAUSED
        self._wake_event.set()

    def resume(self) -> None:
        with self._lock:
            if self._state is not SourceState.PAUSED:
                return
            seeking = self._seek_event.is_set()
            if not seeking:
                self._timeline.resume(self._clock.monotonic_ns())
                self._state = SourceState.PLAYING
        if not seeking:
            self._wake_event.set()
            return
        # A seek issued while paused restarts the pass at the seek target:
        # the interrupted pass's queue may already be drained, so a fresh
        # pass must start from the requested position.
        self._start_pass(publish_restart=True)

    def stop(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                return
        self._halt(SourceState.STOPPED)
        self._publish_reset(ResetReason.SOURCE_RESTARTED)

    def reload(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                raise RuntimeError("Video source is closed")
            path = self._metadata.path
        self._halt(SourceState.STOPPED)
        self._publish_reset(ResetReason.SOURCE_RESTARTED)
        try:
            metadata = inspect_video(path, stream_index=self._stream_index)
        except Exception as error:
            self._fail(f"Could not reload video: {error}")
            raise
        with self._lock:
            self._metadata = metadata
            # Re-evaluate the region against the reloaded file: a longer file
            # may make the previously empty region playable again.
            duration_s = metadata.duration_s
            self._region_error = (
                _describe_region_error(self._loop_start_s, duration_s)
                if duration_s is not None and self._loop_start_s >= duration_s
                else None
            )
            if self._region_error is not None:
                self._last_error = self._region_error
                self._state = SourceState.ERROR
            else:
                self._last_error = None
                self._state = SourceState.READY

    def seek(self, source_time_s: float) -> None:
        """Jump playback to ``source_time_s`` (clamped to the played segment).

        While playing, the decode pass restarts at the new position and the
        presentation timeline re-anchors there. While stopped or paused, the
        position is recorded and the next ``play()`` (or ``resume()``) starts
        from it. The decode thread is the sole consumer of the seek event;
        the presentation thread only observes it, so the call never blocks
        on a full decode queue.
        """

        if not isfinite(source_time_s) or source_time_s < 0.0:
            raise ValueError("seek position must be a finite timestamp at or after 0")
        with self._lock:
            state = self._state
        if state is SourceState.CLOSED:
            raise RuntimeError("Video source is closed")
        target = source_time_s
        with self._lock:
            if self._region_end_s is not None:
                target = min(target, self._region_end_s)
            elif self._metadata.duration_s is not None:
                target = min(target, self._metadata.duration_s)
            target = max(target, self._region_start_s)
            self._seek_target_s = target
            self._position_s = target
            self._seek_filter_pts = target
            self._seek_generation += 1
        self._seek_event.set()
        self._wake_event.set()

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        with self._lock:
            thread = self._presentation_thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout=max(0.0, timeout_s))
        return not thread.is_alive()

    def close(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                return
        self._halt(SourceState.CLOSED)

    def _halt(self, target_state: SourceState) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._join_workers()
        self._clear_decode_queue()
        self._release_containers()
        self._head_in_use = False
        self._head_trigger.clear()
        self._head_queue = queue.Queue(self._head_frame_budget or 0)
        with self._lock:
            self._timeline.reset()
            self._source_frame_index = None
            self._processed_index = 0
            self._state = target_state
            self._seek_event.clear()
            self._seek_filter_pts = None
            if target_state is SourceState.STOPPED:
                # Stopping restarts playback at the segment's beginning.
                self._position_s = self._region_start_s

    def _release_containers(self) -> None:
        """Close the kept-open containers after the workers have stopped.

        Best-effort: a broken file must not keep the source from stopping or
        closing. ``_halt`` is idempotent - already-released containers are
        simply ``None`` on a second call.
        """

        for container in (self._live_container, self._head_container):
            if container is not None:
                with contextlib.suppress(Exception):
                    container.close()
        self._live_container = None
        self._head_container = None

    def _join_workers(self) -> None:
        current = threading.current_thread()
        with self._lock:
            threads = (self._decode_thread, self._presentation_thread, self._head_thread)
        for thread in threads:
            if thread is not None and thread is not current and thread.is_alive():
                thread.join(timeout=5.0)
        survivors = tuple(
            thread
            for thread in threads
            if thread is not None and thread is not current and thread.is_alive()
        )
        if survivors:
            names = ", ".join(thread.name for thread in survivors)
            raise TimeoutError(f"Video worker(s) did not stop within 5 seconds: {names}")
        with self._lock:
            if self._decode_thread is not current:
                self._decode_thread = None
            if self._presentation_thread is not current:
                self._presentation_thread = None
            if self._head_thread is not current:
                self._head_thread = None

    def _decode_loop(self) -> None:
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                passed_any = self._decode_once()
                consecutive_failures = 0
            except Exception as error:
                consecutive_failures += 1
                self._warn(f"Video decode failed: {error}")
                if consecutive_failures >= self._max_decode_failures:
                    self._fail(f"Video decode stopped after repeated failures: {error}")
                    self._queue_put(_QueueSignal.END)
                    return
                continue

            if self._stop_event.is_set():
                return
            if self._seek_event.is_set():
                # A seek interrupted the pass: loop back and decode a new pass
                # from the requested position.
                continue
            # A pass whose range contained no presentable frame (a seek that
            # landed at or beyond the region's end) ends the source cleanly
            # instead of looping: seeking to the end of the played segment
            # must stop playback, and the loop's restart rules apply when the
            # user plays again.
            if not self._loop or not passed_any:
                self._queue_put(_QueueSignal.END)
                return
            # A looping pass restarts from the segment's configured start,
            # not from wherever the previous pass began.
            self._pass_start_s = self._region_start_s
            if not self._queue_put(_QueueSignal.LOOP):
                return

    def _decode_once(self) -> bool:
        """Decode one pass from its start to the region's end.

        Returns whether the pass range contained any presentable frame. A
        range that contains none (a seek that landed at or beyond the
        region's end) is a clean end of pass, not a failure; only a stream
        that yields no frames at all raises.
        """
        start_s = self._pass_start_s
        end_s = self._region_end_s
        if self._seek_event.is_set():
            # The previous pass was interrupted by a seek: restart from the
            # requested position. The decode thread is the sole consumer of
            # the seek event; the presentation thread only observes it.
            start_s = self._seek_target_s
            self._seek_event.clear()
        if end_s is not None and start_s >= end_s:
            # The pass range holds no presentable frame (a seek that landed
            # at or beyond the region's end): a clean end of pass. The kept-
            # open container is left in place; the next pass repositions it.
            return False
        container = self._ensure_live_container()
        stream = container.streams.video[self._stream_index]

        def consume(decoded: DecodedVideoFrame) -> bool:
            return self._queue_put(decoded)

        def interrupted() -> bool:
            # A stop or a mid-pass seek ends this pass; the loop (or the
            # seek's pass) restarts from the requested position.
            return self._stop_event.is_set() or self._seek_event.is_set()

        outcome = run_region_pass(
            container,
            stream,
            start_s=start_s,
            end_s=end_s,
            every_nth=self._process_every_nth_frame,
            consume=consume,
            interrupted=interrupted,
            on_frame_failed=lambda index, error: self._warn(
                f"Skipped source frame {index}: {error}"
            ),
        )
        if outcome.skipped_by_selection:
            with self._lock:
                self._skipped_by_selection += outcome.skipped_by_selection
        if (
            outcome.frames_in_range == 0
            and outcome.frames_decoded == 0
            and not self._seek_event.is_set()
        ):
            raise ValueError("video stream contains no decodable frames")
        return outcome.frames_in_range > 0

    def _ensure_live_container(self) -> Any:
        """The decode thread's container, kept open across passes (ADR-0024).

        The container is re-opened only when the file changes (reload) or
        the source is stopped/closed (``_halt`` releases it after joining
        the workers, so the decode thread is its sole user while alive).
        """

        container = self._live_container
        if container is None:
            container = av.open(str(self._metadata.path), mode="r")
            self._live_container = container
        return container

    def _ensure_head_container(self) -> Any:
        """The head worker's private container (its own decoder state)."""

        container = self._head_container
        if container is None:
            container = av.open(str(self._metadata.path), mode="r")
            self._head_container = container
        return container

    def _head_loop(self) -> None:
        """Keep the loop head warm so a pass boundary presents without a stall.

        ADR-0024: in its own container this worker runs the same shared
        region pass as the live decode thread - same seek, same pre-start
        discard, same every-Nth selection - and stages the first frames of
        the region in a bounded queue. The presentation thread consumes the
        staged head only immediately after a loop signal and drops the
        duplicate frames the restarted live pass decodes (matched by
        ``source_frame_index``). The two passes execute one shared machine
        and cannot drift, which is what the boundary duplicate filter rests on.
        """

        while not self._stop_event.is_set():
            self._head_trigger.wait(timeout=0.05)
            if self._stop_event.is_set():
                return
            self._head_trigger.clear()
            if self._stop_event.is_set():
                return
            budget = self._head_frame_budget
            if budget is None:
                continue
            staged: queue.Queue[DecodedVideoFrame] = queue.Queue(budget)
            staged_count = 0

            def consume(
                decoded: DecodedVideoFrame,
                _staged: queue.Queue[DecodedVideoFrame] = staged,
                _budget: int = budget,
            ) -> bool:
                # The head holds at most ``budget`` frames: the presentation
                # thread serves the first ~1 s of the boundary, then falls
                # back to the live pass. ``_staged``/``_budget`` bind the
                # per-iteration loop variables at definition time.
                nonlocal staged_count
                _staged.put(decoded)
                staged_count += 1
                return staged_count < _budget

            def interrupted() -> bool:
                return self._stop_event.is_set()

            try:
                container = self._ensure_head_container()
                stream = container.streams.video[self._stream_index]
                # The head always pre-decodes from the region start, whatever
                # position a seeked pass takes: the head belongs to the
                # boundary, not to the pass (ADR-0024).
                run_region_pass(
                    container,
                    stream,
                    start_s=self._region_start_s,
                    end_s=self._region_end_s,
                    every_nth=self._process_every_nth_frame,
                    consume=consume,
                    interrupted=interrupted,
                )
            except Exception as error:
                # A head failure degrades the boundary to the restart cost;
                # it must never fail the source.
                self._warn(f"Video loop head pre-decode failed: {error}")
                continue
            if staged.empty():
                # Nothing stageable (empty region or no decodable frames):
                # keep whatever head exists.
                continue
            # Swap the fresh head in only while the presentation is not
            # draining the current one; give up after a bounded wait and let
            # the next trigger retry.
            deadline_ns = self._clock.monotonic_ns() + 2_000_000_000
            while self._clock.monotonic_ns() < deadline_ns:
                if self._stop_event.is_set():
                    return
                with self._lock:
                    if not self._head_in_use:
                        self._head_queue = staged
                        break
                time.sleep(0.02)

    def _presentation_loop(self) -> None:
        # Presentation-local loop-head state: after a loop signal the staged
        # head (captured by reference) is consumed before the restarted live
        # pass's frames; live frames duplicating an already-presented head
        # frame are dropped by source_frame_index.
        head_queue: queue.Queue[DecodedVideoFrame] = queue.Queue(0)
        using_head = False
        head_upto = -1
        while not self._stop_event.is_set():
            if self._claim_pending_seek():
                self._apply_seek()
                using_head = False
                head_upto = -1
            # The staged head must be served before blocking on the decode
            # queue: at a boundary the live pass is still paying its
            # restart, so the decode queue stays empty while the head is
            # ready (ADR-0024).
            if using_head:
                try:
                    head_item = head_queue.get_nowait()
                except queue.Empty:
                    # The staged head is exhausted (or was never staged in
                    # time - the degraded case): fall back to the live
                    # pass's frames, dropping duplicates of the presented
                    # head.
                    using_head = False
                    with self._lock:
                        self._head_in_use = False
                else:
                    if not self._passes_seek_filter(head_item.pts_seconds):
                        continue
                    if not self._present(head_item):
                        return
                    if head_item.source_frame_index > head_upto:
                        head_upto = head_item.source_frame_index
                    continue
            try:
                item = self._decode_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is _QueueSignal.END:
                using_head = False
                with self._lock:
                    self._head_in_use = False
                    if self._state not in {SourceState.CLOSED, SourceState.ERROR}:
                        self._state = SourceState.ENDED
                # Reset the complete source-clock component at natural EOF. Without this
                # lifecycle edge, stateful nodes and MIDI/audio sinks can retain the final
                # frame's desired note state indefinitely.
                self._publish_reset(ResetReason.SOURCE_ENDED)
                return
            if item is _QueueSignal.LOOP:
                with self._lock:
                    self._timeline.reset()
                    self._source_frame_index = None
                    self._processed_index = 0
                    self._head_in_use = True
                    head_queue = self._head_queue
                self._publish_reset(ResetReason.SOURCE_RESTARTED)
                using_head = True
                head_upto = -1
                # The worker refills the head for the next boundary while the
                # presentation is still consuming the current one; it swaps
                # in the fresh head only once this consumption is over.
                self._head_trigger.set()
                continue
            if head_upto >= 0 and item.source_frame_index <= head_upto:
                # A live frame duplicating one already presented from the
                # staged head: the restarted pass re-decodes the head's
                # prefix, so it is dropped instead of presented twice.
                continue
            if not self._passes_seek_filter(item.pts_seconds):
                continue
            if not self._present(item):
                return

    def _apply_seek(self) -> None:
        """Re-anchor the presentation timeline at a seeked position.

        Drops the stale frames the interrupted pass already queued and lets
        the PTS filter drop any stragglers still decoding from that pass, so
        the new pass's first frame lands at the seek point. The staged loop
        head is abandoned (the interrupted pass restarts at the seek target,
        not the region start the head was staged for); the head remains valid
        for the loop boundary that ends the seek pass.
        """

        self._clear_decode_queue()
        with self._lock:
            self._timeline.reset()
            self._source_frame_index = None
            self._processed_index = 0
            self._head_in_use = False
        self._publish_reset(ResetReason.SOURCE_RESTARTED)

    def _passes_seek_filter(self, pts_seconds: float) -> bool:
        with self._lock:
            limit = self._seek_filter_pts
            if limit is None or pts_seconds >= limit:
                self._seek_filter_pts = None
                return True
            return False

    def _claim_pending_seek(self) -> bool:
        """Atomically claim a seek the presentation thread has not applied.

        The generation (not the decode thread's one-shot event) decides, so a
        seek stays visible to the presentation thread even after the decode
        thread consumed the event to restart its pass.
        """

        with self._lock:
            if self._seek_generation != self._applied_seek_generation:
                self._applied_seek_generation = self._seek_generation
                return True
            return False

    def _present(self, frame: DecodedVideoFrame) -> bool:
        target_ns = self._wait_until_due(frame.pts_seconds)
        if target_ns is None:
            return False
        received_ns = self._clock.monotonic_ns()
        with self._lock:
            if self._state is not SourceState.PLAYING or self._stop_event.is_set():
                return False
            self._processed_index += 1
            processed_index = self._processed_index
            self._source_frame_index = frame.source_frame_index
            self._position_s = frame.pts_seconds
        image = build_presented_image_frame(
            node_id=self.node_id,
            source_kind="video",
            rgb_uint8=frame.rgb,
            processed_index=processed_index,
            source_frame_index=frame.source_frame_index,
            source_time_s=frame.pts_seconds,
            received_monotonic_ns=received_ns,
            deadline_monotonic_ns=max(0, target_ns),
        )
        try:
            self._on_frame(PresentedVideoFrame(image, processed_index))
        except Exception as error:
            self._fail(f"Video frame publication failed: {error}")
            self._stop_event.set()
            self._wake_event.set()
            return False
        return True

    def _wait_until_due(self, pts_seconds: float) -> int | None:
        while not self._stop_event.is_set():
            if not self._wait_until_playing():
                return None
            self._wake_event.clear()
            with self._lock:
                if self._stop_event.is_set():
                    return None
                now_ns = self._clock.monotonic_ns()
                target_ns = self._timeline.target_ns(pts_seconds, now_ns)
                if self._state is not SourceState.PLAYING:
                    continue
            remaining_ns = target_ns - self._clock.monotonic_ns()
            if remaining_ns <= 0:
                return target_ns
            self._clock.wait(self._wake_event, remaining_ns / _NANOSECONDS_PER_SECOND)
        return None

    def _wait_until_playing(self) -> bool:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            with self._lock:
                if self._stop_event.is_set():
                    return False
                if self._state is SourceState.PLAYING:
                    return True
                if self._state is not SourceState.PAUSED:
                    return False
            self._clock.wait(self._wake_event, None)
        return False

    def _queue_put(self, item: _DecodeItem) -> bool:
        while not self._stop_event.is_set():
            try:
                self._decode_queue.put(item, timeout=0.05)
            except queue.Full:
                continue
            return True
        return False

    def _clear_decode_queue(self) -> None:
        while True:
            try:
                self._decode_queue.get_nowait()
            except queue.Empty:
                return

    def _warn(self, message: str) -> None:
        with self._lock:
            self._warnings += 1
            self._last_error = message

    def _fail(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._state = SourceState.ERROR

    def _publish_reset(self, reason: ResetReason) -> None:
        if self._on_reset is not None:
            self._on_reset(reason)


__all__ = [
    "DecodedVideoFrame",
    "PlaybackClock",
    "PresentedSourceFrame",
    "PresentedVideoFrame",
    "PtsPlaybackTimeline",
    "SystemPlaybackClock",
    "VideoMetadata",
    "VideoSourceService",
    "inspect_video",
]
