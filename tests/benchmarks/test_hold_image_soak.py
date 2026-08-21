"""Fast checks for the accelerated Hold Image bounded-memory evidence tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.hold_image_soak import run_soak


def test_accelerated_soak_uses_compiled_scheduler_resets_and_bounded_history(
    tmp_path: Path,
) -> None:
    output = tmp_path / "hold-image-soak.json"

    report = run_soak(
        output_path=output,
        total_ticks=120,
        fps=60,
        loop_frames=30,
        delay_frames=4,
        memory_limit_mb=1,
        width=16,
        height=12,
        sample_interval_ticks=20,
        warmup_ticks=40,
        rss_growth_limit_mb=64,
    )

    assert report.passed
    assert report.methodology.accelerated
    assert report.methodology.equivalent_duration_s == 2.0
    assert report.source_loop_reset_count == 4
    assert report.scheduler_error_count == 0
    assert report.hold_image.peak_retained_frame_count == 4
    assert report.hold_image.peak_retained_bytes == 16 * 12 * 3 * 4 * 4
    assert report.hold_image.final_retained_frame_count == 0
    assert report.process_memory.bounded
    samples = {sample.tick_index: sample.retained_frame_count for sample in report.samples}
    assert samples == {0: 0, 20: 4, 40: 4, 60: 0, 80: 4, 100: 4, 120: 0}
    assert output.read_text(encoding="utf-8").endswith("\n")
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["methodology"]["total_ticks"] == 120
    assert data["passed"] is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"total_ticks": 9, "warmup_ticks": 10}, "total_ticks"),
        ({"loop_frames": 3, "delay_frames": 4}, "loop_frames"),
        ({"width": 0}, "width"),
    ],
)
def test_soak_rejects_invalid_configurations(
    tmp_path: Path,
    overrides: dict[str, int],
    message: str,
) -> None:
    arguments: dict[str, int] = {
        "total_ticks": 20,
        "fps": 60,
        "loop_frames": 10,
        "delay_frames": 4,
        "memory_limit_mb": 1,
        "width": 8,
        "height": 8,
        "sample_interval_ticks": 5,
        "warmup_ticks": 10,
        "rss_growth_limit_mb": 64,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        run_soak(output_path=tmp_path / "unused.json", **arguments)
