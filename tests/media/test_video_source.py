"""PyAV video metadata, PTS pacing, selection, and lifecycle tests."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import av
import numpy as np
import pytest
from tools.generate_test_video import generate_test_video, generate_vfr_test_video

from synesthesia_machine.contracts import SourceState
from synesthesia_machine.media import (
    PresentedVideoFrame,
    PtsPlaybackTimeline,
    VideoSourceService,
    inspect_video,
)
from synesthesia_machine.nodes import ExecutionKind, ParameterUpdateMode, ResetReason
from synesthesia_machine.nodes.input import LOAD_VIDEO_TYPE_ID, create_input_definitions

SOURCE_ID = UUID("00000000-0000-0000-0000-000000000321")


class AdvancingClock:
    """Advance exactly by requested presentation waits without wall-clock delay."""

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
            return event.wait(1.0)
        with self._lock:
            self._now_ns += round(timeout_s * 1_000_000_000)
        return event.is_set()


class _RaceClock:
    """Playback clock that gates the unbounded state waits of a paused worker.

    ``wait(event, None)`` is the sleep a paused worker takes between state
    rechecks; holding it until the test releases the gate parks the real
    presentation thread deterministically at the pause-check/wake-clear race
    point, so resume races are driven through the public play/pause/resume
    API alone. Bounded waits advance the fake timeline like ``AdvancingClock``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._now_ns = 0
        self._gate = threading.Event()
        self.state_waits = threading.Event()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def wake_event(self) -> threading.Event:
        return self._wake_event

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def release_gate(self) -> None:
        self._gate.set()

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._now_ns

    def wait(self, event: threading.Event, timeout_s: float | None) -> bool:
        if timeout_s is None:
            self.state_waits.set()
            self._gate.wait(2.0)
            self._gate.clear()
            return event.is_set()
        with self._lock:
            self._now_ns += round(timeout_s * 1_000_000_000)
        return event.is_set()


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_load_video_definition_has_stable_source_contract() -> None:
    definition = create_input_definitions()[0]
    assert definition.type_id == LOAD_VIDEO_TYPE_ID
    assert definition.execution_kind is ExecutionKind.SOURCE
    assert tuple(port.id for port in definition.outputs) == ("image", "processed_index")
    assert definition.parameter("process_every_nth_frame").minimum == 1  # type: ignore[union-attr]
    assert definition.parameter("playback_speed").default == 1.0  # type: ignore[union-attr]
    assert definition.parameter("playback_speed").minimum == 0.25  # type: ignore[union-attr]
    assert definition.parameter("playback_speed").maximum == 4.0  # type: ignore[union-attr]
    assert definition.parameter("playback_speed").update_mode is (  # type: ignore[union-attr]
        ParameterUpdateMode.RESTART_SOURCE
    )
    assert definition.parameter("file_path").update_mode is (  # type: ignore[union-attr]
        ParameterUpdateMode.RESTART_SOURCE
    )


def test_resume_cannot_be_lost_between_pause_check_and_wake_clear(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "resume-race.mp4", frame_count=8)
    clock = _RaceClock()
    presented: list[PresentedVideoFrame] = []
    source = VideoSourceService(SOURCE_ID, path, on_frame=presented.append, clock=clock)
    source.play()
    try:
        source.pause()
        assert clock.state_waits.wait(5.0), "presentation thread never reached the pause gate"
        presented_at_resume = len(presented)
        source.resume()
        clock.release_gate()
        # A resume registered before the worker's wake was released must not
        # be lost: presentation continues after the gate is released.
        assert _wait_until(lambda: len(presented) > presented_at_resume)
    finally:
        clock.release_gate()
        source.close()


def test_inspection_reads_metadata_without_starting_playback(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "metadata.mp4", width=32, height=24, frame_count=4)
    metadata = inspect_video(path)
    received: list[PresentedVideoFrame] = []
    source = VideoSourceService(SOURCE_ID, path, on_frame=received.append)
    try:
        assert metadata.width == 32 and metadata.height == 24
        assert metadata.codec_name == "mpeg4"
        assert source.status().state is SourceState.READY
        assert source.status().processed_index == 0
        assert received == []
    finally:
        source.close()


def test_inspection_accepts_shell_quoted_pasted_video_path(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "quoted path.mkv", width=32, height=24, frame_count=2)

    metadata = inspect_video(f'"{path}"')

    assert metadata.path == path.resolve()
    assert metadata.width == 32 and metadata.height == 24


def test_source_rejects_unavailable_persisted_video_stream(
    tmp_path: Path,
) -> None:
    path = generate_test_video(tmp_path / "single-stream.mp4", frame_count=2)

    with pytest.raises(ValueError, match="stream index 3 is unavailable"):
        VideoSourceService(SOURCE_ID, path, stream_index=3, on_frame=lambda _frame: None)


def test_vfr_pts_and_exact_nth_selection_drive_presentation_timeline(tmp_path: Path) -> None:
    path = generate_vfr_test_video(tmp_path / "vfr.mp4")
    with av.open(str(path), mode="r") as container:
        stream = container.streams.video[0]
        decoded_pts: list[float] = []
        for frame in container.decode(stream):
            assert frame.pts is not None
            assert isinstance(frame.time_base, Fraction)
            decoded_pts.append(float(frame.pts * frame.time_base))
    assert np.allclose(decoded_pts, (0.0, 0.04, 0.12, 0.15, 0.3))

    clock = AdvancingClock()
    received: list[PresentedVideoFrame] = []
    source = VideoSourceService(
        SOURCE_ID,
        path,
        process_every_nth_frame=2,
        on_frame=received.append,
        clock=clock,
    )
    try:
        source.play()
        assert source.wait_until_finished()
        assert [packet.processed_index for packet in received] == [1, 2, 3]
        assert [packet.image.context.source_frame_index for packet in received] == [0, 2, 4]
        assert [packet.image.context.source_time_s for packet in received] == [0.0, 0.12, 0.3]
        assert [packet.image.context.received_monotonic_ns for packet in received] == [
            0,
            120_000_000,
            300_000_000,
        ]
        assert all(packet.image.data.dtype == np.float32 for packet in received)
        assert all(not packet.image.data.flags.writeable for packet in received)
        assert source.status().skipped_by_selection == 2
        assert source.status().state is SourceState.ENDED
    finally:
        source.close()


def test_natural_end_resets_the_source_component(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "end-reset.mp4", frame_count=2)
    resets: list[ResetReason] = []
    source = VideoSourceService(
        SOURCE_ID,
        path,
        on_frame=lambda _frame: None,
        on_reset=resets.append,
        clock=AdvancingClock(),
    )
    try:
        source.play()
        assert source.wait_until_finished()
        assert source.status().state is SourceState.ENDED
        assert resets == [ResetReason.SOURCE_ENDED]
    finally:
        source.close()


def test_pause_shifts_anchor_while_stop_resets_processed_index(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "pause.mp4", frame_count=4, fps=10)
    clock = AdvancingClock()
    resets: list[ResetReason] = []
    first_frame = threading.Event()
    received: list[PresentedVideoFrame] = []
    source: VideoSourceService

    def on_frame(packet: PresentedVideoFrame) -> None:
        received.append(packet)
        if len(received) == 1:
            source.pause()
            first_frame.set()

    source = VideoSourceService(
        SOURCE_ID,
        path,
        on_frame=on_frame,
        on_reset=resets.append,
        clock=clock,
    )
    try:
        source.play()
        assert first_frame.wait(1.0)
        assert source.status().state is SourceState.PAUSED
        assert source.status().processed_index == 1
        assert resets == []
        source.stop()
        assert source.status().state is SourceState.STOPPED
        assert source.status().processed_index == 0
        assert resets == [ResetReason.SOURCE_RESTARTED]
    finally:
        source.close()


def test_pts_timeline_uses_actual_intervals_and_excludes_pause_duration() -> None:
    timeline = PtsPlaybackTimeline()
    assert timeline.target_ns(5.0, 1_000_000_000) == 1_000_000_000
    assert timeline.target_ns(5.04, 1_000_000_000) == 1_040_000_000
    assert timeline.target_ns(5.12, 1_000_000_000) == 1_120_000_000
    timeline.pause(1_020_000_000)
    timeline.resume(3_020_000_000)
    assert timeline.target_ns(5.15, 3_020_000_000) == 3_150_000_000


@pytest.mark.parametrize(
    ("playback_speed", "second_target_ns"),
    [(0.5, 1_080_000_000), (2.0, 1_020_000_000), (4.0, 1_010_000_000)],
)
def test_pts_timeline_scales_media_intervals_by_playback_speed(
    playback_speed: float, second_target_ns: int
) -> None:
    timeline = PtsPlaybackTimeline(playback_speed)

    assert timeline.target_ns(5.0, 1_000_000_000) == 1_000_000_000
    assert timeline.target_ns(5.04, 1_000_000_000) == second_target_ns


@pytest.mark.parametrize("playback_speed", (0.0, -1.0, float("inf"), float("nan")))
def test_video_source_rejects_invalid_playback_speed(tmp_path: Path, playback_speed: float) -> None:
    path = generate_test_video(tmp_path / "invalid-speed.mp4", frame_count=1)

    with pytest.raises(ValueError, match="playback_speed must be finite and positive"):
        VideoSourceService(
            SOURCE_ID,
            path,
            playback_speed=playback_speed,
            on_frame=lambda _frame: None,
        )


def _wait_for_position(
    source: VideoSourceService, at_least_s: float, timeout_s: float = 5.0
) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        position = source.status().source_time_s
        if position is not None and position >= at_least_s:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def test_seek_while_playing_reanchors_and_resets_source_clock(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "seek-playing.mp4", frame_count=12, fps=12)
    clock = AdvancingClock()
    received: list[PresentedVideoFrame] = []
    resets: list[ResetReason] = []
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        on_frame=received.append,
        on_reset=resets.append,
        clock=clock,
    )
    try:
        source.play()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(received) < 2:
            time.sleep(0.01)
        assert len(received) >= 2
        pre_seek_count = len(received)
        source.seek(0.9)
        # The position jumps to the (clamped) target immediately.
        assert source.status().source_time_s == pytest.approx(0.9)
        # The interrupted pass is discarded and the source clock restarts.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and ResetReason.SOURCE_RESTARTED not in resets:
            time.sleep(0.01)
        assert ResetReason.SOURCE_RESTARTED in resets
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if (
                len(received) > pre_seek_count
                and received[-1].image.context.source_time_s >= 0.9 - 1e-6
            ):
                break
            time.sleep(0.01)
        assert len(received) > pre_seek_count
        assert received[-1].image.context.source_time_s >= 0.9 - 1e-6
        assert source.status().state is SourceState.PLAYING
    finally:
        source.close()


def test_seek_while_stopped_starts_playback_at_seek_point(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "seek-stopped.mp4", frame_count=24, fps=12)
    received: list[PresentedVideoFrame] = []
    source = VideoSourceService(SOURCE_ID, path, on_frame=received.append, clock=AdvancingClock())
    try:
        source.seek(1.0)
        assert source.status().source_time_s == pytest.approx(1.0)

        source.play()
        assert source.wait_until_finished()
        assert source.status().state is SourceState.ENDED
        times = [packet.image.context.source_time_s for packet in received]
        assert times and all(t >= 1.0 for t in times)
    finally:
        source.close()


def test_seek_while_paused_applies_on_resume(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "seek-paused.mp4", frame_count=24, fps=12)
    clock = AdvancingClock()
    received: list[PresentedVideoFrame] = []
    paused_at_first = threading.Event()
    source: VideoSourceService

    def on_frame(packet: PresentedVideoFrame) -> None:
        received.append(packet)
        if len(received) == 1:
            source.pause()
            paused_at_first.set()

    source = VideoSourceService(SOURCE_ID, path, loop=True, on_frame=on_frame, clock=clock)
    try:
        source.play()
        assert paused_at_first.wait(5.0)
        source.seek(1.5)
        assert source.status().source_time_s == pytest.approx(1.5)
        source.resume()
        # Resuming after a paused seek restarts the pass at the seek target:
        # the presented frames land at/after it and the source keeps playing.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if received and received[-1].image.context.source_time_s >= 1.5:
                break
            time.sleep(0.01)
        assert received[-1].image.context.source_time_s >= 1.5
        assert source.status().state is SourceState.PLAYING
    finally:
        source.close()


def test_loop_pass_restarts_from_region_start_not_seek_point(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "seek-loop.mp4", frame_count=24, fps=12)
    received: list[PresentedVideoFrame] = []
    resets: list[ResetReason] = []
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        loop_start_s=0.5,
        loop_end_s=1.5,
        on_frame=received.append,
        on_reset=resets.append,
        clock=AdvancingClock(),
    )
    try:
        source.play()
        assert _wait_for_position(source, 0.9)
        source.seek(1.3)
        # The loop pass completes at the region end and restarts at the
        # region start, not at the seek point.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            restarted = resets.count(ResetReason.SOURCE_RESTARTED) >= 2
            if restarted and received:
                last = received[-1].image.context.source_time_s
                if 0.5 - 0.02 <= last <= 0.5 + 1 / 12 + 0.02:
                    break
            time.sleep(0.01)
        last = received[-1].image.context.source_time_s
        assert 0.5 - 0.02 <= last <= 0.5 + 1 / 12 + 0.02
    finally:
        source.close()


def test_seek_clamps_to_the_played_segment(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "seek-clamp.mp4", frame_count=24, fps=12)
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop_start_s=0.5,
        loop_end_s=1.5,
        on_frame=lambda _frame: None,
    )
    try:
        source.seek(0.0)
        assert source.status().source_time_s == pytest.approx(0.5)
        source.seek(100.0)
        assert source.status().source_time_s == pytest.approx(1.5)
    finally:
        source.close()


def test_loop_region_beyond_duration_errors_instead_of_playing_whole_file(
    tmp_path: Path,
) -> None:
    # Timestamps past the video's real duration can be persisted (for
    # instance typed before the engine published the duration). The region
    # then has no frames, so the source must fail with a clear, actionable
    # error instead of silently degrading to the whole file.
    path = generate_test_video(tmp_path / "oob-loop.mp4", frame_count=60, fps=12)  # 5 s video
    received: list[PresentedVideoFrame] = []
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        loop_start_s=20.0,
        loop_end_s=40.0,
        on_frame=received.append,
        clock=AdvancingClock(),
    )
    try:
        status = source.status()
        assert status.state is SourceState.ERROR
        assert status.last_error is not None
        assert "20.00s" in status.last_error
        assert "5.00s" in status.last_error
        # Playing cannot clear a configuration error (a reload with the same
        # configuration would not help either); it must not raise.
        source.play()
        assert source.status().state is SourceState.ERROR
        # A stop and subsequent play re-assert the error instead of starting
        # a pass over the whole file.
        source.stop()
        source.play()
        assert source.status().state is SourceState.ERROR
        assert source.status().last_error is not None
        time.sleep(0.1)
        assert not received
    finally:
        source.close()


@pytest.mark.parametrize(
    ("start", "end", "expected_start", "expected_end"),
    [
        pytest.param(0.5, 1.5, 0.5, 1.5, id="in-range-untouched"),
        pytest.param(1.0, 40.0, 1.0, 5.0, id="end-clamped-to-duration"),
    ],
)
def test_loop_region_clamps_to_the_video_duration(
    tmp_path: Path, start: float, end: float, expected_start: float, expected_end: float
) -> None:
    path = generate_test_video(tmp_path / "region.mp4", frame_count=60, fps=12)  # 5 s video
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop_start_s=start,
        loop_end_s=end,
        on_frame=lambda _frame: None,
    )
    try:
        assert source._region_start_s == pytest.approx(expected_start, abs=0.05)  # pyright: ignore[reportPrivateUsage]
        assert source._region_end_s == pytest.approx(expected_end, abs=0.05)  # pyright: ignore[reportPrivateUsage]
    finally:
        source.close()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        pytest.param(20.0, 40.0, id="both-beyond-duration"),
        pytest.param(40.0, 0.0, id="start-beyond-zero-end"),
    ],
)
def test_loop_region_start_beyond_duration_is_rejected(
    tmp_path: Path, start: float, end: float
) -> None:
    # A start timestamp at or past the video's end leaves an empty region:
    # the source reports a configuration error instead of degrading to the
    # whole file (which would silently ignore both timestamps).
    path = generate_test_video(tmp_path / "region-reject.mp4", frame_count=60, fps=12)
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        loop_start_s=start,
        loop_end_s=end,
        on_frame=lambda _frame: None,
    )
    try:
        status = source.status()
        assert status.state is SourceState.ERROR
        assert status.last_error is not None
    finally:
        source.close()


def test_loop_region_start_at_exactly_duration_is_rejected(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "region-edge.mp4", frame_count=60, fps=12)
    probe = VideoSourceService(SOURCE_ID, path, on_frame=lambda _frame: None)
    duration_s = probe.metadata.duration_s
    probe.close()
    assert duration_s is not None
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        loop_start_s=duration_s,
        loop_end_s=duration_s + 10.0,
        on_frame=lambda _frame: None,
    )
    try:
        assert source.status().state is SourceState.ERROR
        assert source.status().last_error is not None
    finally:
        source.close()


@pytest.mark.parametrize("position", (-1.0, float("inf"), float("nan")))
def test_seek_rejects_invalid_positions(tmp_path: Path, position: float) -> None:
    path = generate_test_video(tmp_path / "seek-invalid.mp4", frame_count=2)
    source = VideoSourceService(SOURCE_ID, path, on_frame=lambda _frame: None)
    try:
        with pytest.raises(ValueError, match="finite"):
            source.seek(position)
    finally:
        source.close()


def test_seek_rejects_closed_source(tmp_path: Path) -> None:
    path = generate_test_video(tmp_path / "seek-closed.mp4", frame_count=2)
    source = VideoSourceService(SOURCE_ID, path, on_frame=lambda _frame: None)
    source.close()

    with pytest.raises(RuntimeError, match="closed"):
        source.seek(0.1)


def test_loop_boundary_gap_is_at_most_one_frame_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A natural loop boundary must not stall for the pass-restart cost.

    The live decode path is artificially slowed to just under the presentation
    pacing (one fixed cost per frame, modelling a complex stream - a phone
    video with a long GOP - whose live decode cannot run far ahead of the
    bounded presentation queue). Only the live worker pays that cost; the
    pre-decode worker decodes at native speed, mirroring how the head's
    dedicated runway - the whole tail of the previous pass - hides a
    restart cost the live path cannot hide at the boundary moment.

    Without the ADR-0024 kept-open container and pre-decoded loop head, the
    boundary gap is the full restart cost (reopen + seek + the whole
    pre-keyframe discard: 200 frames x 38 ms here), far beyond one frame
    interval. With the head, the restarted pass's first frames are already
    staged and the boundary presents within one frame interval. The 40 ms
    bound is the fixture's frame interval (25 fps at 1x).
    """

    import synesthesia_machine.media.video_source as video_source_module

    path = generate_test_video(
        tmp_path / "boundary.mp4",
        width=64,
        height=48,
        frame_count=400,
        fps=25,
        codec="libx264",
        # A single keyframe at t=0 forces every pass restart to discard the
        # whole pre-region prefix (200 frames for the 8 s region start), so
        # the natural boundary gap without a pre-decoded head is the full
        # restart cost, far beyond one frame interval.
        codec_options={"keyint_min": "400", "keyint_max": "400", "scenecut": "0"},
    )
    real_av = video_source_module.av

    class _SlowLiveContainer:
        """Delegates to a real container but slows only the live decode thread.

        The live worker's thread name is the one handle this test has to
        distinguish it from the pre-decode worker: only frames yielded to the
        live thread pay the per-frame cost, which is exactly the restart cost
        the loop head exists to hide.
        """

        def __init__(self, container: object) -> None:
            self._container = container
            self._decode = container.decode

        def decode(self, *args: object, **kwargs: object) -> object:
            def stream_frames() -> object:
                for frame in self._decode(*args, **kwargs):
                    # 38 ms: just under the 40 ms presentation interval, so
                    # the live path keeps up but cannot run far ahead of
                    # presentation (the queue stays empty most of the pass).
                    if threading.current_thread().name.startswith("video-decode-"):
                        time.sleep(0.038)
                    yield frame

            return stream_frames()

        def __getattr__(self, name: str) -> object:
            return getattr(self._container, name)

        def __enter__(self) -> _SlowLiveContainer:
            return self

        def __exit__(self, *exc: object) -> bool:
            self._container.close()
            return False

    class _SlowLiveAv:
        time_base = real_av.time_base

        def open(self, *args: object, **kwargs: object) -> _SlowLiveContainer:
            return _SlowLiveContainer(real_av.open(*args, **kwargs))

    monkeypatch.setattr(video_source_module, "av", _SlowLiveAv())
    received: list[tuple[int, int, float]] = []
    resets: list[ResetReason] = []

    def on_frame(frame: PresentedVideoFrame) -> None:
        received.append(
            (time.perf_counter_ns(), frame.processed_index, frame.image.context.source_time_s)
        )

    # The default SystemPlaybackClock paces presentation in wall time - the
    # boundary stall is a wall-time phenomenon and must not be elided.
    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        loop_start_s=8.0,
        loop_end_s=12.0,
        on_frame=on_frame,
        on_reset=resets.append,
    )
    source.play()
    try:
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            # Three boundaries need four pass starts (processed_index resets
            # to 0 at each restart, so each pass's first frame is index 1).
            if sum(1 for _, index, _ in received if index == 1) >= 4:
                break
            time.sleep(0.05)
        pass_starts = [i for i, (_, index, _) in enumerate(received) if index == 1]
        assert len(pass_starts) >= 4, "the loop never completed four passes"

        frame_interval_ns = 1_000_000_000 // 25  # 40 ms at 25 fps
        for i in range(1, min(4, len(pass_starts))):
            boundary = pass_starts[i]
            gap_ns = received[boundary][0] - received[boundary - 1][0]
            assert gap_ns <= frame_interval_ns, (
                f"boundary {i} stalled {gap_ns / 1e6:.0f} ms across the loop "
                f"boundary (bound: one 40 ms frame interval)"
            )
            # The restarted pass presents the region start with a fresh tick.
            assert received[boundary][2] == pytest.approx(8.0, abs=0.04 + 0.02)
            assert resets.count(ResetReason.SOURCE_RESTARTED) >= i
    finally:
        source.close()


def test_loop_boundary_stays_seamless_when_prefix_frames_lack_pts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head worker's frame index must stay aligned with the live pass's.

    Some streams yield frames without a usable PTS in the pre-keyframe
    prefix. The live pass counts such frames toward frame selection (and
    never presents them - conversion rejects them), so the pre-decode
    worker must mirror that counting; otherwise the source_frame_index
    duplicate filter at the boundary drops nothing and the loop head is
    presented twice.

    The shim nulls the PTS of the first 100 frames every decoder yields
    (both workers restart from the same single keyframe, so they see the
    same PTS-less prefix). A misaligned head re-presents the staged head
    from the live pass: the presented PTS series then drops from the
    head's end - well before the region end - back to the region start
    mid-pass, which the assertions below reject. Only a legitimate wrap
    (a drop from the region end to the region start) may drop.
    """

    import synesthesia_machine.media.video_source as video_source_module

    path = generate_test_video(
        tmp_path / "prefix-pts.mp4",
        width=64,
        height=48,
        frame_count=400,
        fps=25,
        codec="libx264",
        codec_options={"keyint_min": "400", "keyint_max": "400", "scenecut": "0"},
    )
    real_av = video_source_module.av

    class _PrefixPtsNoneContainer:
        """Nulls the PTS of the first 100 frames every decoder yields."""

        PREFIX_FRAMES = 100

        def __init__(self, container: object) -> None:
            self._container = container
            self._decode = container.decode

        def decode(self, *args: object, **kwargs: object) -> object:
            def stream_frames() -> object:
                yielded = 0
                for frame in self._decode(*args, **kwargs):
                    if yielded < self.PREFIX_FRAMES:
                        frame.pts = None
                        yielded += 1
                    yield frame

            return stream_frames()

        def __getattr__(self, name: str) -> object:
            return getattr(self._container, name)

        def __enter__(self) -> _PrefixPtsNoneContainer:
            return self

        def __exit__(self, *exc: object) -> bool:
            self._container.close()
            return False

    class _PrefixPtsNoneAv:
        time_base = real_av.time_base

        def open(self, *args: object, **kwargs: object) -> _PrefixPtsNoneContainer:
            return _PrefixPtsNoneContainer(real_av.open(*args, **kwargs))

    monkeypatch.setattr(video_source_module, "av", _PrefixPtsNoneAv())
    received: list[tuple[int, int, float]] = []

    def on_frame(frame: PresentedVideoFrame) -> None:
        received.append(
            (
                time.perf_counter_ns(),
                frame.processed_index,
                frame.image.context.source_time_s,
            )
        )

    source = VideoSourceService(
        SOURCE_ID,
        path,
        loop=True,
        loop_start_s=8.0,
        loop_end_s=12.0,
        on_frame=on_frame,
    )
    source.play()
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            # Three pass starts (two boundaries): the PTS-less prefix makes
            # every pass pay the misalignment, not just the first.
            if sum(1 for _, index, _ in received if index == 1) >= 3:
                break
            time.sleep(0.05)
        pass_starts = [i for i, (_, index, _) in enumerate(received) if index == 1]
        assert len(pass_starts) >= 3, "the loop never completed three passes"

        region_end = 12.0
        interval = 0.04
        for (_prev_wall, _prev_index, prev_pts), (_, _, cur_pts) in pairwise(received):
            if cur_pts < prev_pts - 1e-9:
                assert prev_pts >= region_end - 2 * interval, (
                    f"PTS dropped {prev_pts:.2f} -> {cur_pts:.2f} mid-pass: the loop "
                    "head was presented twice (head worker index misaligned with "
                    "the live pass)"
                )
    finally:
        source.close()
