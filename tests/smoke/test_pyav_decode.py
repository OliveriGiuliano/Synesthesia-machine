"""Generated-media PyAV acceptance test with no external fixture dependency."""

from pathlib import Path

from tools.generate_test_video import DEFAULT_FRAME_COUNT, generate_test_video
from tools.pyav_probe import probe_video


def test_pyav_decodes_generated_video_with_timestamps(tmp_path: Path) -> None:
    video_path = generate_test_video(tmp_path / "tiny.mp4")
    result = probe_video(video_path)

    assert result.decoded_frames == DEFAULT_FRAME_COUNT
    assert result.first_pts == 0
    assert result.first_time_seconds == 0.0
    assert result.first_rgb_shape == (48, 64, 3)
    assert result.first_rgb_dtype == "uint8"

    video_path.unlink()
    assert not video_path.exists()
