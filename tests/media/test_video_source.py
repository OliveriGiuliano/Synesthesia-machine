"""PyAV video metadata, PTS pacing, selection, and lifecycle tests."""

from __future__ import annotations

import threading
from fractions import Fraction
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

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._now_ns

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool:
        if timeout_s is None:
            return wake_event.wait(1.0)
        with self._lock:
            self._now_ns += round(timeout_s * 1_000_000_000)
        return wake_event.is_set()


class _BlockingClearEvent(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self.clear_started = threading.Event()
        self.allow_clear = threading.Event()

    def clear(self) -> None:
        self.clear_started.set()
        assert self.allow_clear.wait(1.0)
        super().clear()


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
    path = generate_test_video(tmp_path / "resume-race.mp4", frame_count=1)
    source = VideoSourceService(
        SOURCE_ID, path, on_frame=lambda _frame: None, clock=AdvancingClock()
    )
    wake_event = _BlockingClearEvent()
    source._wake_event = wake_event  # pyright: ignore[reportPrivateUsage]
    with source._lock:  # pyright: ignore[reportPrivateUsage]
        source._state = SourceState.PAUSED  # pyright: ignore[reportPrivateUsage]
    result: list[bool] = []
    waiter = threading.Thread(
        target=lambda: result.append(
            source._wait_until_playing()  # pyright: ignore[reportPrivateUsage]
        )
    )
    try:
        waiter.start()
        assert wake_event.clear_started.wait(1.0)
        source.resume()
        wake_event.allow_clear.set()
        waiter.join(0.2)
        assert not waiter.is_alive()
        assert result == [True]
    finally:
        wake_event.allow_clear.set()
        source._stop_event.set()  # pyright: ignore[reportPrivateUsage]
        wake_event.set()
        waiter.join(1.0)
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
