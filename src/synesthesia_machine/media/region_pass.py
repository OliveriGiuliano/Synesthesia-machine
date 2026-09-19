"""The shared region pass: seek, discard, select, convert - written once.

One playback pass over a region of a video file is: seek to the keyframe at
or before the pass start, decode-and-discard the frames before the start,
select every Nth frame in the range, and convert the selected frames. The
live decode thread and the loop-head pre-decoder (ADR-0024) both run this
single state machine over their own container adapters, so the head can no
longer drift from the live pass: the mirror contract the boundary duplicate
filter relies on rests on one implementation instead of two hand-kept
copies.

A container (a real PyAV handle or a test fake) exposes only the surface the
pass drives: ``seek`` and ``decode``. A frame exposes only ``pts``,
``time_base``, and the conversion call. Everything else - the selection
ordinal, the discard comparisons, the failure policy - lives here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import av
import numpy as np
from numpy.typing import NDArray


@runtime_checkable
class DecodeContainer(Protocol):
    """The container surface the region pass drives.

    PyAV containers satisfy it; test fakes implement the same two methods
    (the container shims in ``tests/media/test_video_source.py`` already do,
    and ``tests/media/test_region_pass.py`` drives the pass headlessly with
    fully synthetic ones).
    """

    def seek(self, offset: int, *, stream: Any | None = None) -> None:
        """Position at ``offset`` (stream time base, or container microseconds)."""
        ...

    def decode(self, stream: Any) -> Iterable[Any]:
        """Decode video frames from the current position."""
        ...


@dataclass(frozen=True, slots=True)
class DecodedVideoFrame:
    """Selected decoded frame before its presentation deadline is reached."""

    source_frame_index: int
    pts_seconds: float
    rgb: NDArray[np.uint8]

    def __post_init__(self) -> None:
        if self.source_frame_index < 0:
            raise ValueError("source_frame_index cannot be negative")
        if not np.isfinite(self.pts_seconds):
            raise ValueError("frame PTS must be finite")
        if self.rgb.dtype != np.uint8 or self.rgb.ndim != 3 or self.rgb.shape[2] != 3:
            raise ValueError("decoded video frames must be HxWx3 uint8 RGB")
        if not self.rgb.flags.c_contiguous:
            raise ValueError("decoded video frames must be C-contiguous")


@dataclass(frozen=True, slots=True)
class RegionPassOutcome:
    """The frame-count facts one pass reports to its consumer thread.

    ``frames_decoded`` counts every frame the container yielded (including
    the pre-start discard); ``frames_in_range`` counts frames inside the
    pass range (PTS-less frames included); ``skipped_by_selection`` counts
    in-range frames the every-Nth rule dropped; ``frames_presented`` counts
    frames handed to ``consume`` (including the one whose ``False`` return
    ended the pass). ``interrupted`` reports the pass ended early - a
    source stop/seek, or the consumer's output cap - rather than reaching
    the region's end.
    """

    frames_decoded: int
    frames_in_range: int
    skipped_by_selection: int
    frames_presented: int
    interrupted: bool


def seek_container(container: DecodeContainer, stream: Any, start_s: float) -> None:
    """Seek to the keyframe at or before ``start_s`` (the ``stream``-relative
    offset keeps the demuxer on the right stream). PyAV ships no type
    stubs, so both handles are ``Any`` at this third-party boundary."""

    time_base = stream.time_base
    if time_base is not None:
        container.seek(int(start_s / time_base), stream=stream)
    else:
        # Container timestamps are microseconds (``av.time_base``).
        container.seek(int(start_s * 1_000_000))


def _convert_frame(frame: av.VideoFrame, source_frame_index: int) -> DecodedVideoFrame:
    if frame.pts is None or frame.time_base is None:
        raise ValueError("frame has no usable presentation timestamp")
    pts_seconds = float(frame.pts * frame.time_base)
    rgb = np.ascontiguousarray(
        frame.to_ndarray(format="rgb24"),
        dtype=np.uint8,
    )
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"unexpected RGB frame shape {rgb.shape}")
    return DecodedVideoFrame(source_frame_index, pts_seconds, rgb)


def run_region_pass(
    container: DecodeContainer,
    stream: Any,
    *,
    start_s: float,
    end_s: float | None,
    every_nth: int,
    consume: Callable[[DecodedVideoFrame], bool],
    interrupted: Callable[[], bool],
    on_frame_failed: Callable[[int, Exception], None] | None = None,
) -> RegionPassOutcome:
    """Run one region pass over ``container``; hand selected frames to ``consume``.

    The pass seeks to the keyframe at or before ``start_s``, decodes and
    discards the frames before the start (the seek lands on the previous
    keyframe), stops once past ``end_s`` when one is given, and converts
    every ``every_nth``th in-range frame. A frame without a usable PTS
    counts toward the selection ordinal but can never convert, so it is
    never presented - the live pass and the head both depend on that exact
    counting (ADR-0024).

    ``consume`` returning False ends the pass early (a full output or the
    consumer's own cap); ``interrupted`` returning True ends it between
    frames (a source stop or a mid-pass seek). The two hooks keep each
    consumer's per-frame checks and per-frame sink outside the shared
    machine; ``on_frame_failed`` (when given) is called for a selected
    frame that fails to convert.
    """
    seek_container(container, stream, start_s)
    frames_decoded = 0
    frames_in_range = 0
    skipped_by_selection = 0
    frames_presented = 0
    source_frame_index = 0
    for frame in container.decode(stream):
        frames_decoded += 1
        if interrupted():
            return RegionPassOutcome(
                frames_decoded,
                frames_in_range,
                skipped_by_selection,
                frames_presented,
                True,
            )
        if frame.pts is not None and frame.time_base is not None:
            pts_seconds = float(frame.pts * frame.time_base)
            if pts_seconds < start_s:
                # Frames before the pass start (the seek landed on the
                # previous keyframe): decode through, present none.
                continue
            if end_s is not None and pts_seconds > end_s:
                break
        frames_in_range += 1
        if source_frame_index % every_nth == 0:
            try:
                decoded = _convert_frame(frame, source_frame_index)
            except (TypeError, ValueError) as error:
                if on_frame_failed is not None:
                    on_frame_failed(source_frame_index, error)
            else:
                frames_presented += 1
                if not consume(decoded):
                    return RegionPassOutcome(
                        frames_decoded,
                        frames_in_range,
                        skipped_by_selection,
                        frames_presented,
                        True,
                    )
        else:
            skipped_by_selection += 1
        source_frame_index += 1
    return RegionPassOutcome(
        frames_decoded,
        frames_in_range,
        skipped_by_selection,
        frames_presented,
        False,
    )
