"""Deterministic PyAV file playback with source-owned worker threads.

The service owns decode and presentation.  It publishes compact presented packets to
an engine-owned latest-frame mailbox, so graph execution can drop stale work without
changing the source's PTS-driven playback timeline.
"""

from __future__ import annotations

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
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    NoDataType,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.media_path import normalize_media_path
from synesthesia_machine.nodes.base import ResetReason

_NANOSECONDS_PER_SECOND = 1_000_000_000
_DEFAULT_DECODE_QUEUE_SIZE = 2
_DEFAULT_MAX_DECODE_FAILURES = 3


class PlaybackClock(Protocol):
    """Injectable monotonic clock and interruptible wait used by PTS pacing."""

    def monotonic_ns(self) -> int: ...

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool: ...


class SystemPlaybackClock:
    def monotonic_ns(self) -> int:
        return time.perf_counter_ns()

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool:
        return wake_event.wait(timeout_s)


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


@dataclass(frozen=True, slots=True)
class DecodedVideoFrame:
    """Selected decoded frame before its presentation deadline is reached."""

    source_frame_index: int
    pts_seconds: float
    rgb: NDArray[np.float32]

    def __post_init__(self) -> None:
        if self.source_frame_index < 0:
            raise ValueError("source_frame_index cannot be negative")
        if not np.isfinite(self.pts_seconds):
            raise ValueError("frame PTS must be finite")
        if self.rgb.dtype != np.float32 or self.rgb.ndim != 3 or self.rgb.shape[2] != 3:
            raise ValueError("decoded video frames must be HxWx3 float32 RGB")
        if self.rgb.flags.writeable or not self.rgb.flags.c_contiguous:
            raise ValueError("decoded video frames must be read-only and C-contiguous")


@dataclass(frozen=True, slots=True, init=False)
class PresentedSourceFrame:
    """One image or explicit outage tick ready for the engine source mailbox."""

    image: ImageFrame | NoDataType
    processed_index: int
    context: FrameContext

    def __init__(
        self,
        image: ImageFrame | NoDataType,
        processed_index: int,
        context: FrameContext | None = None,
    ) -> None:
        resolved_context = image.context if isinstance(image, ImageFrame) else context
        if resolved_context is None:
            raise ValueError("NoData source frames require an explicit frame context")
        object.__setattr__(self, "image", image)
        object.__setattr__(self, "processed_index", processed_index)
        object.__setattr__(self, "context", resolved_context)
        if self.processed_index < 1:
            raise ValueError("processed_index must start at 1")
        if self.context.tick_index != self.processed_index:
            raise ValueError("source tick and processed index must match")
        if isinstance(self.image, ImageFrame) and self.image.context != self.context:
            raise ValueError("image and source-frame contexts must match")


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
        # "until the end of the video". An empty or inverted region falls back
        # to [0, loop_end_s) (or the whole file) so the source always has a
        # playable segment instead of failing to decode.
        duration_s = self._metadata.duration_s
        if loop_end_s > 0.0:
            region_end_s: float | None = loop_end_s
        elif duration_s is not None:
            region_end_s = duration_s
        else:
            region_end_s = None
        region_start_s = loop_start_s
        if duration_s is not None:
            region_start_s = min(region_start_s, duration_s)
        if region_end_s is not None and region_start_s >= region_end_s:
            region_start_s = 0.0
            if region_end_s <= 0.0:
                region_end_s = None
        self._region_start_s = region_start_s
        self._region_end_s = region_end_s
        self._pass_start_s = region_start_s

        self._lock = threading.RLock()
        self._decode_queue: queue.Queue[_DecodeItem] = queue.Queue(decode_queue_size)
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._timeline = PtsPlaybackTimeline(playback_speed)
        self._decode_thread: threading.Thread | None = None
        self._presentation_thread: threading.Thread | None = None
        self._state = SourceState.READY
        self._source_frame_index: int | None = None
        self._processed_index = 0
        self._skipped_by_selection = 0
        self._warnings = 0
        self._last_error: str | None = None
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
            raise RuntimeError("Video source must be reloaded after an error")
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
                self._pass_start_s = self._seek_target_s
                self._seek_event.clear()
            else:
                self._pass_start_s = self._region_start_s
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
            presentation_thread = self._presentation_thread
            decode_thread = self._decode_thread
        if publish_restart:
            self._publish_reset(ResetReason.SOURCE_RESTARTED)
        presentation_thread.start()
        decode_thread.start()

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

    def _join_workers(self) -> None:
        current = threading.current_thread()
        with self._lock:
            threads = (self._decode_thread, self._presentation_thread)
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

    def _decode_loop(self) -> None:
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                self._decode_once()
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
            if not self._loop:
                self._queue_put(_QueueSignal.END)
                return
            # A looping pass restarts from the segment's configured start,
            # not from wherever the previous pass began.
            self._pass_start_s = self._region_start_s
            if not self._queue_put(_QueueSignal.LOOP):
                return

    def _decode_once(self) -> None:
        decoded_any = False
        start_s = self._pass_start_s
        end_s = self._region_end_s
        if self._seek_event.is_set():
            # The previous pass was interrupted by a seek: restart from the
            # requested position. The decode thread is the sole consumer of
            # the seek event; the presentation thread only observes it.
            start_s = self._seek_target_s
            self._seek_event.clear()
        with av.open(str(self._metadata.path), mode="r") as container:
            stream = container.streams.video[self._stream_index]
            if start_s > 0.0:
                self._seek_container(container, stream, start_s)
            source_frame_index = 0
            for frame in container.decode(stream):  # pyright: ignore[reportUnknownMemberType]
                if self._stop_event.is_set():
                    return
                if self._seek_event.is_set():
                    # A seek arrived mid-pass: end this pass so the next one
                    # starts from the requested position.
                    return
                if frame.pts is not None and frame.time_base is not None:
                    pts_seconds = float(frame.pts * frame.time_base)
                    if pts_seconds < start_s:
                        # Frames before the segment start (the seek lands on
                        # the previous keyframe): decode through, present none.
                        continue
                    if end_s is not None and pts_seconds > end_s:
                        # The segment is complete; the container is dropped
                        # when this pass ends.
                        break
                decoded_any = True
                if source_frame_index % self._process_every_nth_frame != 0:
                    with self._lock:
                        self._skipped_by_selection += 1
                    source_frame_index += 1
                    continue
                try:
                    decoded = _convert_frame(frame, source_frame_index)
                except (TypeError, ValueError) as error:
                    self._warn(f"Skipped source frame {source_frame_index}: {error}")
                    source_frame_index += 1
                    continue
                if not self._queue_put(decoded):
                    return
                source_frame_index += 1
        if not decoded_any and not self._seek_event.is_set():
            raise ValueError("video stream contains no decodable frames")

    @staticmethod
    def _seek_container(container: Any, stream: Any, start_s: float) -> None:
        """Seek to the keyframe at or before ``start_s`` (the ``stream``-relative
        offset keeps the demuxer on the right stream). PyAV ships no type
        stubs, so both handles are ``Any`` at this third-party boundary."""

        time_base = stream.time_base
        if time_base is not None:
            container.seek(int(start_s / time_base), stream=stream)
        else:
            # Container timestamps are microseconds (``av.time_base``).
            container.seek(int(start_s * 1_000_000))

    def _presentation_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._claim_pending_seek():
                self._apply_seek()
            try:
                item = self._decode_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is _QueueSignal.END:
                with self._lock:
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
                self._publish_reset(ResetReason.SOURCE_RESTARTED)
                continue
            if not self._passes_seek_filter(item.pts_seconds):
                continue
            if not self._present(item):
                return

    def _apply_seek(self) -> None:
        """Re-anchor the presentation timeline at a seeked position.

        Drops the stale frames the interrupted pass already queued and lets
        the PTS filter drop any stragglers still decoding from that pass, so
        the new pass's first frame lands at the seek point.
        """

        self._clear_decode_queue()
        with self._lock:
            self._timeline.reset()
            self._source_frame_index = None
            self._processed_index = 0
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
        context = FrameContext(
            clock_id=self.node_id,
            tick_index=processed_index,
            source_frame_index=frame.source_frame_index,
            source_time_s=max(0.0, frame.pts_seconds),
            received_monotonic_ns=received_ns,
            deadline_monotonic_ns=max(0, target_ns),
            is_realtime=True,
        )
        image = ImageFrame(
            data=frame.rgb,
            color_space=ColorSpace.SRGB,
            channel_names=("R", "G", "B"),
            alpha_mode=AlphaMode.NONE,
            context=context,
            provenance=FrameProvenance(self.node_id, "video"),
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


def _convert_frame(frame: av.VideoFrame, source_frame_index: int) -> DecodedVideoFrame:
    if frame.pts is None or frame.time_base is None:
        raise ValueError("frame has no usable presentation timestamp")
    pts_seconds = float(frame.pts * frame.time_base)
    rgb_uint8 = np.asarray(frame.to_ndarray(format="rgb24"), dtype=np.uint8)
    if rgb_uint8.ndim != 3 or rgb_uint8.shape[2] != 3:
        raise ValueError(f"unexpected RGB frame shape {rgb_uint8.shape}")
    rgb = np.ascontiguousarray(rgb_uint8, dtype=np.float32)
    rgb *= np.float32(1.0 / 255.0)
    rgb.flags.writeable = False
    return DecodedVideoFrame(source_frame_index, pts_seconds, rgb)


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
