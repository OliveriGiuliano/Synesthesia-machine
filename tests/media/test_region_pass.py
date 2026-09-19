"""Headless tests for the shared region pass.

The pass drives only the container's ``seek``/``decode`` surface and the
frame's ``pts``/``time_base``/conversion surface, so these tests run it
against fully synthetic fakes: no real encoder, no PyAV handles, no
threads. This is the test surface both the live decode thread and the
loop-head pre-decoder rest on - the machine itself, not the consumers.

Counting semantics pinned here: ``frames_decoded`` counts every frame the
container yielded (including the frame that triggered a stop or a past-end
break); ``frames_in_range`` counts frames that survived the range gate
(PTS-less frames included).
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from synesthesia_machine.media.region_pass import (
    DecodedVideoFrame,
    RegionPassOutcome,
    run_region_pass,
    seek_container,
)

_TB = Fraction(1, 30)


class _FakeFrame:
    """A frame exposing only the surface the pass reads (plus conversion)."""

    def __init__(
        self,
        pts: int | None,
        time_base: Any = _TB,
        rgb: np.ndarray | None = None,
        fail: bool = False,
    ) -> None:
        self.pts = pts
        self.time_base = time_base
        self._rgb = rgb if rgb is not None else np.full((2, 3, 3), 7, dtype=np.uint8)
        self._fail = fail

    def to_ndarray(self, format: str | None = None) -> np.ndarray:
        if self._fail:
            raise ValueError("synthetic conversion failure")
        return self._rgb


class _FakeStream:
    def __init__(self, time_base: Any = _TB) -> None:
        self.time_base = time_base


class _FakeContainer:
    """A container exposing only ``seek`` and ``decode``."""

    def __init__(self, frames: list[_FakeFrame], stream: _FakeStream | None = None) -> None:
        self._frames = list(frames)
        self.streams = SimpleNamespace(video=[stream if stream is not None else _FakeStream()])
        self.seek_offsets: list[tuple[int, Any]] = []

    @property
    def stream(self) -> _FakeStream:
        return self.streams.video[0]

    def seek(self, offset: int, *, stream: Any | None = None) -> None:
        self.seek_offsets.append((offset, stream))

    def decode(self, stream: Any) -> list[_FakeFrame]:
        assert stream is self.streams.video[0]
        return self._frames


def _frames_at_seconds(*seconds: float) -> list[_FakeFrame]:
    return [_FakeFrame(round(s * 30), _TB) for s in seconds]


def _run(
    container: _FakeContainer,
    *,
    start_s: float,
    end_s: float | None = None,
    every_nth: int = 1,
    presented: list[DecodedVideoFrame] | None = None,
    failed: list[tuple[int, Exception]] | None = None,
    interrupted: Callable[[], bool] | None = None,
    consume: Callable[[DecodedVideoFrame], bool] | None = None,
) -> RegionPassOutcome:
    sink: list[DecodedVideoFrame] = presented if presented is not None else []
    on_failed = (lambda index, error: failed.append((index, error))) if failed is not None else None
    return run_region_pass(
        container,
        container.stream,
        start_s=start_s,
        end_s=end_s,
        every_nth=every_nth,
        consume=consume if consume is not None else (lambda frame: sink.append(frame) or True),
        interrupted=interrupted if interrupted is not None else (lambda: False),
        on_frame_failed=on_failed,
    )


def test_seek_uses_stream_time_base() -> None:
    container = _FakeContainer([])
    run_region_pass(
        container,
        container.stream,
        start_s=1.0,
        end_s=None,
        every_nth=1,
        consume=lambda frame: False,
        interrupted=lambda: False,
    )
    offset, used_stream = container.seek_offsets[0]
    assert used_stream is container.stream
    assert offset == int(1.0 / _TB) == 30


def test_seek_without_time_base_uses_container_microseconds() -> None:
    stream = _FakeStream(time_base=None)
    container = _FakeContainer([], stream=stream)
    seek_container(container, stream, 0.25)
    assert container.seek_offsets == [(250_000, None)]


def test_discards_prefix_and_selects_every_nth() -> None:
    pts = [i / 30 for i in range(12)]  # 0.0 .. 0.367
    container = _FakeContainer(_frames_at_seconds(*pts))
    presented: list[DecodedVideoFrame] = []
    outcome = _run(container, start_s=0.1, every_nth=2, presented=presented)
    # In range: pts 3..11 (9 frames, the frame at exactly start_s included).
    # Selection ordinals 0, 2, 4, 6, 8 -> pts 3, 5, 7, 9, 11.
    assert [f.pts_seconds for f in presented] == [i / 30 for i in (3, 5, 7, 9, 11)]
    assert [f.source_frame_index for f in presented] == [0, 2, 4, 6, 8]
    assert outcome == RegionPassOutcome(
        frames_decoded=12,
        frames_in_range=9,
        skipped_by_selection=4,
        frames_presented=5,
        interrupted=False,
    )


def test_stops_past_region_end() -> None:
    pts = [i / 30 for i in range(12)]
    container = _FakeContainer(_frames_at_seconds(*pts))
    presented: list[DecodedVideoFrame] = []
    outcome = _run(container, start_s=0.1, end_s=0.2, presented=presented)
    # end_s is inclusive: pts 3..6 (0.1..0.2) are in range; the pass stops
    # at the first frame past the end (pts 7), which still counts as
    # decoded.
    assert [f.pts_seconds for f in presented] == [i / 30 for i in (3, 4, 5, 6)]
    assert outcome == RegionPassOutcome(
        frames_decoded=8,
        frames_in_range=4,
        skipped_by_selection=0,
        frames_presented=4,
        interrupted=False,
    )


def test_untimestamped_frame_counts_toward_selection_but_is_never_presented() -> None:
    frames = [
        _FakeFrame(None, _TB),  # ordinal 0: selected, conversion fails
        _FakeFrame(1, _TB),  # ordinal 1: dropped by the every-2nd rule
        _FakeFrame(2, _TB),  # ordinal 2: selected and presented
    ]
    container = _FakeContainer(frames)
    presented: list[DecodedVideoFrame] = []
    failed: list[tuple[int, Exception]] = []
    outcome = _run(container, start_s=0.0, every_nth=2, presented=presented, failed=failed)
    assert [f.source_frame_index for f in presented] == [2]
    assert [index for index, _ in failed] == [0]
    assert outcome == RegionPassOutcome(
        frames_decoded=3,
        frames_in_range=3,
        skipped_by_selection=1,
        frames_presented=1,
        interrupted=False,
    )


def test_consumer_cap_ends_pass_early() -> None:
    pts = [i / 30 for i in range(12)]
    container = _FakeContainer(_frames_at_seconds(*pts))
    accepted = 0

    def consume(frame: DecodedVideoFrame) -> bool:
        nonlocal accepted
        accepted += 1
        return accepted < 3

    outcome = _run(container, start_s=0.0, consume=consume)
    # The frame whose consume returned False counts as handed to the
    # consumer; the pass does not decode further.
    assert outcome == RegionPassOutcome(
        frames_decoded=3,
        frames_in_range=3,
        skipped_by_selection=0,
        frames_presented=3,
        interrupted=True,
    )


def test_interrupted_flag_stops_between_frames() -> None:
    pts = [i / 30 for i in range(12)]
    container = _FakeContainer(_frames_at_seconds(*pts))
    checks = 0

    def interrupted() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    presented: list[DecodedVideoFrame] = []
    outcome = _run(container, start_s=0.0, presented=presented, interrupted=interrupted)
    # The third frame is interrupted before the range gate, so it is
    # decoded but not in range.
    assert [f.pts_seconds for f in presented] == [0.0, 1 / 30]
    assert outcome == RegionPassOutcome(
        frames_decoded=3,
        frames_in_range=2,
        skipped_by_selection=0,
        frames_presented=2,
        interrupted=True,
    )


def test_failed_selected_frame_reports_and_pass_continues() -> None:
    frames = [
        _FakeFrame(0, _TB),
        _FakeFrame(1, _TB, fail=True),
        _FakeFrame(2, _TB),
    ]
    container = _FakeContainer(frames)
    presented: list[DecodedVideoFrame] = []
    failed: list[tuple[int, Exception]] = []
    outcome = _run(container, start_s=0.0, presented=presented, failed=failed)
    assert [f.source_frame_index for f in presented] == [0, 2]
    assert [index for index, _ in failed] == [1]
    assert isinstance(failed[0][1], ValueError)
    assert outcome.frames_presented == 2
    assert outcome.interrupted is False


def test_empty_container_reports_zero_frames() -> None:
    container = _FakeContainer([])
    outcome = _run(container, start_s=0.0)
    assert outcome == RegionPassOutcome(
        frames_decoded=0,
        frames_in_range=0,
        skipped_by_selection=0,
        frames_presented=0,
        interrupted=False,
    )


def test_range_with_no_in_range_frames_reports_zero() -> None:
    pts = [i / 30 for i in range(12)]
    container = _FakeContainer(_frames_at_seconds(*pts))
    outcome = _run(container, start_s=0.35, end_s=0.25)
    # start > end: every frame is either before the start (discarded) or
    # past the end (stop) - nothing is in range, but the container yields
    # its whole tail (the stop frame counts as decoded).
    assert outcome.frames_in_range == 0
    assert outcome.frames_presented == 0
    assert outcome.frames_decoded == 12


def test_decoded_frame_validation_rejects_bad_payloads() -> None:
    with pytest.raises(ValueError, match="finite"):
        DecodedVideoFrame(0, float("inf"), np.zeros((2, 3, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="negative"):
        DecodedVideoFrame(-1, 0.0, np.zeros((2, 3, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="HxWx3"):
        DecodedVideoFrame(0, 0.0, np.zeros((2, 3, 3, 4), dtype=np.uint8))
