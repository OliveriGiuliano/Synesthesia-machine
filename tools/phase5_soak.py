"""Run the accelerated Phase 5 Hold Image bounded-memory soak and record evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import psutil

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import ResetReason
from synesthesia_machine.runtime import PortKey, Scheduler
from tools.environment_report import EnvironmentReport, collect_environment_report

DEFAULT_OUTPUT_PATH = Path("docs/phase-5-soak.json")
DEFAULT_TICKS = 30 * 60 * 60
DEFAULT_FPS = 60
DEFAULT_LOOP_FRAMES = 10 * DEFAULT_FPS
DEFAULT_DELAY_FRAMES = 8
DEFAULT_MEMORY_LIMIT_MB = 16
DEFAULT_WIDTH = 64
DEFAULT_HEIGHT = 64
DEFAULT_SAMPLE_INTERVAL = 6_000
DEFAULT_WARMUP_TICKS = 6_000
DEFAULT_RSS_GROWTH_LIMIT_MB = 32
_MEBIBYTE = 1024 * 1024

DOCUMENT_ID = UUID("53000000-0000-0000-0000-000000000000")
SOURCE_ID = UUID("53000000-0000-0000-0000-000000000001")
HOLD_ID = UUID("53000000-0000-0000-0000-000000000002")
DISPLAY_ID = UUID("53000000-0000-0000-0000-000000000003")


@dataclass(frozen=True, slots=True)
class MemorySample:
    tick_index: int
    rss_bytes: int
    retained_frame_count: int
    retained_bytes: int


@dataclass(frozen=True, slots=True)
class SoakMethodology:
    scope: str
    accelerated: bool
    total_ticks: int
    target_fps: int
    equivalent_duration_s: float
    equivalent_duration_minutes: float
    source_loop_frames: int
    warmup_ticks: int
    sample_interval_ticks: int
    frame_shape: tuple[int, int, int]
    criteria: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HoldImageResult:
    node_id: str
    delay_frames: int
    frame_bytes: int
    estimated_retained_bytes: int
    memory_limit_bytes: int
    peak_retained_frame_count: int
    peak_retained_bytes: int
    final_retained_frame_count: int
    final_retained_bytes: int
    capacity_respected: bool


@dataclass(frozen=True, slots=True)
class ProcessMemoryResult:
    start_rss_bytes: int
    post_warmup_rss_bytes: int
    final_rss_bytes: int
    peak_sampled_rss_bytes: int
    post_warmup_growth_bytes: int
    post_warmup_span_bytes: int
    growth_limit_bytes: int
    bounded: bool


@dataclass(frozen=True, slots=True)
class Phase5SoakReport:
    generated_at_utc: str
    git_commit: str | None
    git_working_tree_dirty: bool | None
    environment: EnvironmentReport
    methodology: SoakMethodology
    wall_clock_duration_s: float
    source_loop_reset_count: int
    scheduler_error_count: int
    hold_image: HoldImageResult
    process_memory: ProcessMemoryResult
    passed: bool
    samples: tuple[MemorySample, ...]


def _git_state() -> tuple[str | None, bool | None]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    return (
        commit.stdout.strip() if commit.returncode == 0 else None,
        bool(status.stdout.strip()) if status.returncode == 0 else None,
    )


def _compiled_scheduler(*, delay_frames: int, memory_limit_mb: int) -> Scheduler:
    registry = create_application_registry()
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("synmachine.input.load_video", node_id=SOURCE_ID)
    document.add_node(
        "synmachine.image.hold_image",
        parameters={
            "delay_frames": delay_frames,
            "memory_limit_mb": memory_limit_mb,
        },
        node_id=HOLD_ID,
    )
    document.add_node("synmachine.visualization.display_image_data", node_id=DISPLAY_ID)
    document.add_connection(SOURCE_ID, "image", HOLD_ID, "image")
    document.add_connection(HOLD_ID, "image", DISPLAY_ID, "image")
    compilation = GraphCompiler(registry).compile(document.snapshot())
    if compilation.plan is None:
        messages = "; ".join(issue.message for issue in compilation.report.errors)
        raise RuntimeError(f"Phase 5 soak graph did not compile: {messages}")
    return Scheduler(compilation.plan)


def _frame(context: FrameContext, *, width: int, height: int) -> ImageFrame:
    value = np.float32((context.tick_index % 256) / 255.0)
    data = np.full((height, width, 3), value, dtype=np.float32)
    data.flags.writeable = False
    return ImageFrame(
        data,
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(SOURCE_ID, "phase5-accelerated-soak"),
    )


def _validate_configuration(
    *,
    total_ticks: int,
    fps: int,
    loop_frames: int,
    delay_frames: int,
    memory_limit_mb: int,
    width: int,
    height: int,
    sample_interval_ticks: int,
    warmup_ticks: int,
    rss_growth_limit_mb: int,
) -> None:
    positive = {
        "total_ticks": total_ticks,
        "fps": fps,
        "loop_frames": loop_frames,
        "delay_frames": delay_frames,
        "memory_limit_mb": memory_limit_mb,
        "width": width,
        "height": height,
        "sample_interval_ticks": sample_interval_ticks,
        "warmup_ticks": warmup_ticks,
        "rss_growth_limit_mb": rss_growth_limit_mb,
    }
    for name, value in positive.items():
        if value < 1:
            raise ValueError(f"{name} must be at least 1")
    if total_ticks < warmup_ticks:
        raise ValueError("total_ticks must be greater than or equal to warmup_ticks")
    if total_ticks < delay_frames:
        raise ValueError("total_ticks must be at least delay_frames")
    if loop_frames < delay_frames:
        raise ValueError("loop_frames must be at least delay_frames so Hold Image reaches capacity")


def run_soak(
    *,
    output_path: Path,
    total_ticks: int = DEFAULT_TICKS,
    fps: int = DEFAULT_FPS,
    loop_frames: int = DEFAULT_LOOP_FRAMES,
    delay_frames: int = DEFAULT_DELAY_FRAMES,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    sample_interval_ticks: int = DEFAULT_SAMPLE_INTERVAL,
    warmup_ticks: int = DEFAULT_WARMUP_TICKS,
    rss_growth_limit_mb: int = DEFAULT_RSS_GROWTH_LIMIT_MB,
) -> Phase5SoakReport:
    """Execute a production-compiled graph without sleeping and write bounded-memory evidence."""

    _validate_configuration(
        total_ticks=total_ticks,
        fps=fps,
        loop_frames=loop_frames,
        delay_frames=delay_frames,
        memory_limit_mb=memory_limit_mb,
        width=width,
        height=height,
        sample_interval_ticks=sample_interval_ticks,
        warmup_ticks=warmup_ticks,
        rss_growth_limit_mb=rss_growth_limit_mb,
    )
    frame_bytes = width * height * 3 * np.dtype(np.float32).itemsize
    estimated_retained_bytes = frame_bytes * delay_frames
    memory_limit_bytes = memory_limit_mb * _MEBIBYTE
    if estimated_retained_bytes > memory_limit_bytes:
        raise ValueError("configured Hold Image estimate exceeds memory_limit_mb")

    environment = collect_environment_report()
    git_commit, git_dirty = _git_state()
    process = psutil.Process()
    scheduler = _compiled_scheduler(
        delay_frames=delay_frames,
        memory_limit_mb=memory_limit_mb,
    )
    samples: list[MemorySample] = []
    scheduler_error_count = 0
    source_loop_reset_count = 0
    peak_retained_frame_count = 0
    peak_retained_bytes = 0

    started = time.perf_counter()

    def sample(tick_index: int) -> MemorySample:
        diagnostic = scheduler.node_memory_diagnostics(HOLD_ID)[0]
        item = MemorySample(
            tick_index=tick_index,
            rss_bytes=process.memory_info().rss,
            retained_frame_count=diagnostic.retained_frame_count,
            retained_bytes=diagnostic.retained_bytes,
        )
        samples.append(item)
        return item

    start_sample = sample(0)
    try:
        for tick_index in range(1, total_ticks + 1):
            source_frame_index = (tick_index - 1) % loop_frames
            context = FrameContext(
                SOURCE_ID,
                tick_index,
                source_frame_index,
                source_frame_index / fps,
                tick_index,
                None,
                False,
            )
            image = _frame(context, width=width, height=height)
            result = scheduler.execute_tick(
                context,
                source_values={
                    PortKey(SOURCE_ID, "image"): image,
                    PortKey(SOURCE_ID, "processed_index"): tick_index,
                },
            )
            scheduler_error_count += len(result.errors)
            del result, image

            should_inspect = (
                tick_index <= delay_frames
                or tick_index % loop_frames == 0
                or tick_index in (warmup_ticks, total_ticks)
                or tick_index % sample_interval_ticks == 0
            )
            if should_inspect:
                diagnostic = scheduler.node_memory_diagnostics(HOLD_ID)[0]
                peak_retained_frame_count = max(
                    peak_retained_frame_count, diagnostic.retained_frame_count
                )
                peak_retained_bytes = max(peak_retained_bytes, diagnostic.retained_bytes)
                if diagnostic.retained_frame_count > delay_frames:
                    raise RuntimeError(
                        "Hold Image retained more frames than its configured capacity"
                    )
                if diagnostic.retained_bytes > estimated_retained_bytes:
                    raise RuntimeError("Hold Image retained more bytes than its exact estimate")

            if tick_index % loop_frames == 0:
                scheduler.reset_source(SOURCE_ID, ResetReason.SOURCE_RESTARTED)
                source_loop_reset_count += 1
                reset_diagnostic = scheduler.node_memory_diagnostics(HOLD_ID)[0]
                if reset_diagnostic.retained_frame_count != 0:
                    raise RuntimeError("Hold Image did not release history at a source-loop reset")

            if (
                tick_index in (warmup_ticks, total_ticks) or tick_index % sample_interval_ticks == 0
            ) and samples[-1].tick_index != tick_index:
                sample(tick_index)

        elapsed_s = time.perf_counter() - started
        final_diagnostic = scheduler.node_memory_diagnostics(HOLD_ID)[0]
    finally:
        scheduler.close(ResetReason.ENGINE_RESTARTED)

    warmup_sample = next(item for item in samples if item.tick_index == warmup_ticks)
    final_sample = samples[-1]
    post_warmup_samples = tuple(item for item in samples if item.tick_index >= warmup_ticks)
    rss_values = tuple(item.rss_bytes for item in post_warmup_samples)
    rss_growth_limit_bytes = rss_growth_limit_mb * _MEBIBYTE
    rss_growth = max(0, final_sample.rss_bytes - warmup_sample.rss_bytes)
    rss_span = max(rss_values) - min(rss_values)
    rss_bounded = rss_growth <= rss_growth_limit_bytes and rss_span <= rss_growth_limit_bytes
    capacity_respected = (
        peak_retained_frame_count == delay_frames
        and peak_retained_bytes == estimated_retained_bytes
        and final_diagnostic.retained_frame_count <= delay_frames
        and final_diagnostic.retained_bytes <= estimated_retained_bytes
    )
    passed = scheduler_error_count == 0 and capacity_respected and rss_bounded
    report = Phase5SoakReport(
        generated_at_utc=datetime.now(UTC).isoformat(),
        git_commit=git_commit,
        git_working_tree_dirty=git_dirty,
        environment=environment,
        methodology=SoakMethodology(
            scope=(
                "Production GraphCompiler and Scheduler path: injected Load Video values to "
                "Hold Image to Display Image Data"
            ),
            accelerated=True,
            total_ticks=total_ticks,
            target_fps=fps,
            equivalent_duration_s=total_ticks / fps,
            equivalent_duration_minutes=total_ticks / fps / 60.0,
            source_loop_frames=loop_frames,
            warmup_ticks=warmup_ticks,
            sample_interval_ticks=sample_interval_ticks,
            frame_shape=(height, width, 3),
            criteria=(
                "No scheduler errors occur.",
                "Hold Image reaches but never exceeds its exact frame/byte capacity.",
                "Every simulated source-loop reset releases retained Hold Image references.",
                f"Post-warmup RSS growth and sampled span are each <= {rss_growth_limit_mb} MiB.",
            ),
            limitations=(
                "Ticks are accelerated without sleeping; this is equivalent source-clock duration, "
                "not 30 minutes of wall-clock operation.",
                "The source boundary is injected deterministically, avoiding decoder and hardware "
                "effects while exercising the production compiled scheduler path.",
                "ResetReason.SOURCE_RESTARTED represents the current scheduler's source-loop reset "
                "boundary; there is no separate SOURCE_LOOP enum value.",
                "Process RSS is sampled at comparable post-reset lifecycle points; exact Hold "
                "Image capacity is checked separately before every reset.",
                "RSS is the sole process-memory criterion and includes allocator and "
                "native-library behavior visible to the OS.",
                "RSS is allocator- and OS-dependent, so the gate uses a documented post-warmup "
                "growth/span allowance rather than exact equality or a Python-heap sub-gate.",
            ),
        ),
        wall_clock_duration_s=elapsed_s,
        source_loop_reset_count=source_loop_reset_count,
        scheduler_error_count=scheduler_error_count,
        hold_image=HoldImageResult(
            node_id=str(HOLD_ID),
            delay_frames=delay_frames,
            frame_bytes=frame_bytes,
            estimated_retained_bytes=estimated_retained_bytes,
            memory_limit_bytes=memory_limit_bytes,
            peak_retained_frame_count=peak_retained_frame_count,
            peak_retained_bytes=peak_retained_bytes,
            final_retained_frame_count=final_diagnostic.retained_frame_count,
            final_retained_bytes=final_diagnostic.retained_bytes,
            capacity_respected=capacity_respected,
        ),
        process_memory=ProcessMemoryResult(
            start_rss_bytes=start_sample.rss_bytes,
            post_warmup_rss_bytes=warmup_sample.rss_bytes,
            final_rss_bytes=final_sample.rss_bytes,
            peak_sampled_rss_bytes=max(item.rss_bytes for item in samples),
            post_warmup_growth_bytes=rss_growth,
            post_warmup_span_bytes=rss_span,
            growth_limit_bytes=rss_growth_limit_bytes,
            bounded=rss_bounded,
        ),
        passed=passed,
        samples=tuple(samples),
    )
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(asdict(report), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--loop-frames", type=int, default=DEFAULT_LOOP_FRAMES)
    parser.add_argument("--delay-frames", type=int, default=DEFAULT_DELAY_FRAMES)
    parser.add_argument("--memory-limit-mb", type=int, default=DEFAULT_MEMORY_LIMIT_MB)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--sample-interval", type=int, default=DEFAULT_SAMPLE_INTERVAL)
    parser.add_argument("--warmup-ticks", type=int, default=DEFAULT_WARMUP_TICKS)
    parser.add_argument("--rss-growth-limit-mb", type=int, default=DEFAULT_RSS_GROWTH_LIMIT_MB)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_soak(
        output_path=args.output,
        total_ticks=args.ticks,
        fps=args.fps,
        loop_frames=args.loop_frames,
        delay_frames=args.delay_frames,
        memory_limit_mb=args.memory_limit_mb,
        width=args.width,
        height=args.height,
        sample_interval_ticks=args.sample_interval,
        warmup_ticks=args.warmup_ticks,
        rss_growth_limit_mb=args.rss_growth_limit_mb,
    )
    print(
        f"Phase 5 accelerated soak: ticks={report.methodology.total_ticks}, "
        f"equivalent_minutes={report.methodology.equivalent_duration_minutes:.1f}, "
        f"wall_seconds={report.wall_clock_duration_s:.3f}, "
        f"loops={report.source_loop_reset_count}, passed={report.passed}"
    )
    print(args.output.resolve())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
