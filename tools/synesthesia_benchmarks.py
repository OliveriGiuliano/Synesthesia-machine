"""Benchmark the four synthesis algorithms on deterministic 500x500 fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    MidiStateFrame,
    read_only_float32,
)
from synesthesia_machine.midi import resolve_musical_selector
from synesthesia_machine.nodes.synesthesia.edges_to_pitch import (
    CENTROID_X,
    EDGE_STRENGTH,
    EXTERNAL,
    edges_to_midi_state,
)
from synesthesia_machine.nodes.synesthesia.fourier import (
    BAND_PERCENTILE,
    HANN,
    HORIZONTAL,
    FourierShapeCache,
    fourier_to_midi_state,
)
from synesthesia_machine.nodes.synesthesia.musical import CommonMusicalSettings
from synesthesia_machine.nodes.synesthesia.optical_flow import (
    CELL_NOTES,
    DIRECTION,
    FAST,
    MEAN_MAGNITUDE,
    optical_flow_to_midi_state,
)
from synesthesia_machine.nodes.synesthesia.scanline import MEAN, scanline_to_midi_state
from tools.environment_report import EnvironmentReport, collect_environment_report

DEFAULT_OUTPUT_PATH = Path("docs/evidence/synesthesia-benchmarks.json")
DEFAULT_WARMUP_RUNS = 3
DEFAULT_MEASURED_RUNS = 10
FIXTURE_WIDTH = 500
FIXTURE_HEIGHT = 500

CLOCK_ID = UUID("66000000-0000-0000-0000-000000000000")
SOURCE_ID = UUID("66000000-0000-0000-0000-000000000001")
SCANLINE_ID = UUID("66000000-0000-0000-0000-000000000002")
EDGES_ID = UUID("66000000-0000-0000-0000-000000000003")
FOURIER_ID = UUID("66000000-0000-0000-0000-000000000004")
OPTICAL_FLOW_ID = UUID("66000000-0000-0000-0000-000000000005")


@dataclass(frozen=True, slots=True)
class NoteState:
    channel_1_based: int
    note: int
    velocity: int


@dataclass(frozen=True, slots=True)
class TimingStatistics:
    median_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float
    samples_ms: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class AlgorithmBenchmark:
    algorithm: str
    fixture: str
    fixture_sha256: str
    input_shape: tuple[int, int]
    warmup_runs: int
    measured_runs: int
    output_notes: tuple[NoteState, ...]
    output_state_stable: bool
    timing: TimingStatistics
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkMethodology:
    scope: str
    input_resolution: tuple[int, int]
    timer: str
    criteria: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SynesthesiaBenchmarkReport:
    schema_version: int
    generated_at_utc: str
    git_commit: str | None
    git_working_tree_dirty: bool | None
    environment: EnvironmentReport
    methodology: BenchmarkMethodology
    benchmarks: tuple[AlgorithmBenchmark, ...]
    passed: bool


BenchmarkRunner = Callable[[], MidiStateFrame]


def run_benchmarks(
    *,
    output_path: Path,
    warmup_runs: int = DEFAULT_WARMUP_RUNS,
    measured_runs: int = DEFAULT_MEASURED_RUNS,
) -> SynesthesiaBenchmarkReport:
    """Run deterministic algorithm microbenchmarks and write canonical JSON evidence."""

    _validate_configuration(warmup_runs=warmup_runs, measured_runs=measured_runs)
    context = _context()
    settings = _musical_settings()
    scanline = _scanline_fixture(context)
    edges = _edges_fixture(context)
    fourier = _fourier_fixture(context)
    optical_current, optical_reference = _optical_flow_fixtures(context)
    fourier_cache = FourierShapeCache()

    benchmarks = (
        _measure(
            algorithm="Scanline",
            fixture=(
                "Finite normalized horizontal gradient with deterministic impulses; middle "
                "five-row mean band."
            ),
            fixture_digest=_array_digest(scanline.data),
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
            runner=lambda: scanline_to_midi_state(
                scanline,
                row_index=FIXTURE_HEIGHT // 2,
                line_thickness=5,
                aggregation=MEAN,
                activation_threshold=0.15,
                velocity_curve_exponent=1.25,
                settings=settings,
                node_id=SCANLINE_ID,
                context=context,
            ),
        ),
        _measure(
            algorithm="Edges to Pitch",
            fixture=(
                "Finite binary edge mask containing two rectangles, one circle, and one ellipse."
            ),
            fixture_digest=_array_digest(edges.data),
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
            runner=lambda: edges_to_midi_state(
                edges,
                retrieval_mode=EXTERNAL,
                minimum_contour_area=16.0,
                minimum_contour_perimeter=16.0,
                contour_limit=32,
                pitch_feature=CENTROID_X,
                velocity_feature=EDGE_STRENGTH,
                pitch_minimum=0.0,
                pitch_maximum=1.0,
                velocity_minimum=0.0,
                velocity_maximum=1.0,
                settings=settings,
                node_id=EDGES_ID,
                context=context,
            ),
        ),
        _measure(
            algorithm="Fourier",
            fixture="Finite normalized 8-cycle horizontal sinusoid with a 3-cycle vertical term.",
            fixture_digest=_array_digest(fourier.data),
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
            runner=lambda: fourier_to_midi_state(
                fourier,
                window=HANN,
                subtract_mean=True,
                frequency_mapping=HORIZONTAL,
                frequency_minimum=0.0,
                frequency_maximum=0.05,
                amplitude_floor=0.0,
                amplitude_ceiling=10.0,
                dc_exclusion_radius=0.0,
                band_aggregation=BAND_PERCENTILE,
                percentile=100.0,
                activation_threshold=0.01,
                settings=settings,
                node_id=FOURIER_ID,
                context=context,
                cache=fourier_cache,
            ),
            diagnostic_factory=lambda: (
                f"Fourier shape-cache misses={fourier_cache.misses}, hits={fourier_cache.hits}."
            ),
        ),
        _measure(
            algorithm="Optical Flow",
            fixture=(
                "Seeded blurred texture translated four pixels right with reflected borders; "
                "reference-to-current flow."
            ),
            fixture_digest=_combined_digest(optical_reference.data, optical_current.data),
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
            runner=lambda: optical_flow_to_midi_state(
                optical_current,
                optical_reference,
                flow_preset=FAST,
                minimum_motion_magnitude=0.25,
                pitch_feature=DIRECTION,
                velocity_feature=MEAN_MAGNITUDE,
                grid_rows=4,
                grid_columns=4,
                aggregation=CELL_NOTES,
                magnitude_minimum=0.0,
                magnitude_maximum=8.0,
                settings=settings,
                node_id=OPTICAL_FLOW_ID,
                context=context,
            ),
        ),
    )
    git_commit, git_dirty = _git_state()
    passed = all(item.output_state_stable and bool(item.output_notes) for item in benchmarks)
    report = SynesthesiaBenchmarkReport(
        schema_version=1,
        generated_at_utc=datetime.now(UTC).isoformat(),
        git_commit=git_commit,
        git_working_tree_dirty=git_dirty,
        environment=collect_environment_report(),
        methodology=BenchmarkMethodology(
            scope=(
                "Direct Qt-free algorithm calls over pre-built immutable synthetic "
                "fixtures; fixture creation and JSON serialization are outside timed regions."
            ),
            input_resolution=(FIXTURE_HEIGHT, FIXTURE_WIDTH),
            timer="time.perf_counter_ns; elapsed samples reported in milliseconds",
            criteria=(
                "Each of the four required algorithms runs on a 500x500 input.",
                "Every warm-up and measured invocation returns the same non-empty MIDI note state.",
                "All measured durations are finite and non-negative.",
                "Fourier reuses one shape cache across warm-up and measured invocations.",
            ),
            limitations=(
                "This is per-algorithm microbenchmark evidence, not a full graph throughput test.",
                "It does not include decoder, scheduler, process transport, UI preview, audio, or "
                "MIDI hardware costs.",
                "It does not implement the architecture section 18.6 release methodology of a "
                "five-second warm-up, 30-second measurement, source/processed FPS, drop and "
                "latency telemetry, and median comparison across three runs.",
                "Wall-clock timings vary by hardware, power mode, OS scheduling, and concurrent "
                "load; deterministic output states, not timing equality, are the correctness gate.",
            ),
        ),
        benchmarks=benchmarks,
        passed=passed,
    )
    destination = output_path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(asdict(report), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def _measure(
    *,
    algorithm: str,
    fixture: str,
    fixture_digest: str,
    warmup_runs: int,
    measured_runs: int,
    runner: BenchmarkRunner,
    diagnostic_factory: Callable[[], str] | None = None,
) -> AlgorithmBenchmark:
    expected: tuple[NoteState, ...] | None = None
    stable = True
    for _ in range(warmup_runs):
        notes = _note_state(runner())
        expected, stable = _compare_state(expected, notes, stable)

    samples: list[float] = []
    for _ in range(measured_runs):
        started_ns = time.perf_counter_ns()
        notes = _note_state(runner())
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
        if not math.isfinite(elapsed_ms) or elapsed_ms < 0.0:
            raise RuntimeError(f"{algorithm} produced an invalid timing sample")
        samples.append(elapsed_ms)
        expected, stable = _compare_state(expected, notes, stable)

    if expected is None:
        raise RuntimeError(f"{algorithm} did not produce an output state")
    if not stable:
        raise RuntimeError(f"{algorithm} output state changed between benchmark invocations")
    ordered = tuple(sorted(samples))
    return AlgorithmBenchmark(
        algorithm=algorithm,
        fixture=fixture,
        fixture_sha256=fixture_digest,
        input_shape=(FIXTURE_HEIGHT, FIXTURE_WIDTH),
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
        output_notes=expected,
        output_state_stable=stable,
        timing=TimingStatistics(
            median_ms=_percentile(ordered, 50.0),
            p95_ms=_percentile(ordered, 95.0),
            minimum_ms=ordered[0],
            maximum_ms=ordered[-1],
            samples_ms=tuple(samples),
        ),
        diagnostic=None if diagnostic_factory is None else diagnostic_factory(),
    )


def _context() -> FrameContext:
    return FrameContext(CLOCK_ID, 1, 0, 0.0, 1, None, False)


def _musical_settings() -> CommonMusicalSettings:
    return CommonMusicalSettings(
        resolve_musical_selector("C", "chromatic", 60, 71),
        0,
        8,
        20,
        110,
    )


def _scanline_fixture(context: FrameContext) -> ChannelFrame:
    x = np.linspace(0.0, 1.0, FIXTURE_WIDTH, dtype=np.float32)
    line = np.clip(x + np.float32(0.2) * (np.arange(FIXTURE_WIDTH) % 79 == 0), 0.0, 1.0)
    values = np.zeros((FIXTURE_HEIGHT, FIXTURE_WIDTH), dtype=np.float32)
    center = FIXTURE_HEIGHT // 2
    values[center - 2 : center + 3] = line
    return _channel(values, context)


def _edges_fixture(context: FrameContext) -> ChannelFrame:
    values = np.zeros((FIXTURE_HEIGHT, FIXTURE_WIDTH), dtype=np.float32)
    cv2.rectangle(values, (35, 45), (155, 205), 1.0, thickness=3)
    cv2.rectangle(values, (280, 65), (450, 170), 1.0, thickness=5)
    cv2.circle(values, (170, 360), 70, 1.0, thickness=4)
    cv2.ellipse(values, (365, 350), (80, 45), 25.0, 0.0, 360.0, 1.0, thickness=4)
    return _channel(values, context)


def _fourier_fixture(context: FrameContext) -> ChannelFrame:
    x = np.arange(FIXTURE_WIDTH, dtype=np.float64)[None, :]
    y = np.arange(FIXTURE_HEIGHT, dtype=np.float64)[:, None]
    values = 0.5 + 0.35 * np.sin(2.0 * np.pi * 8.0 * x / FIXTURE_WIDTH)
    values = values + 0.15 * np.sin(2.0 * np.pi * 3.0 * y / FIXTURE_HEIGHT)
    return _channel(np.asarray(values, dtype=np.float32), context)


def _optical_flow_fixtures(context: FrameContext) -> tuple[ImageFrame, ImageFrame]:
    generator = np.random.default_rng(6606)
    texture = generator.random((FIXTURE_HEIGHT, FIXTURE_WIDTH), dtype=np.float32)
    texture = cast(
        NDArray[np.float32],
        cv2.GaussianBlur(texture, (0, 0), sigmaX=1.2, sigmaY=1.2),
    )
    reference = np.ascontiguousarray(np.repeat(texture[..., None], 3, axis=2), dtype=np.float32)
    transform = np.array([[1.0, 0.0, 4.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    current = np.ascontiguousarray(
        cast(
            NDArray[np.float32],
            cv2.warpAffine(
                reference,
                transform,
                (FIXTURE_WIDTH, FIXTURE_HEIGHT),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            ),
        ),
        dtype=np.float32,
    )
    return _image(current, context), _image(reference, context)


def _channel(values: NDArray[np.float32], context: FrameContext) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(values),
        ChannelSemantic.LUMINANCE,
        0.0,
        1.0,
        False,
        context,
    )


def _image(values: NDArray[np.float32], context: FrameContext) -> ImageFrame:
    return ImageFrame(
        read_only_float32(values),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(SOURCE_ID, "synesthesia-benchmark"),
    )


def _note_state(frame: MidiStateFrame) -> tuple[NoteState, ...]:
    return tuple(
        NoteState(key.channel + 1, key.note, velocity)
        for key, velocity in sorted(frame.notes.items(), key=lambda item: item[0])
    )


def _compare_state(
    expected: tuple[NoteState, ...] | None,
    actual: tuple[NoteState, ...],
    stable: bool,
) -> tuple[tuple[NoteState, ...], bool]:
    return (actual, stable) if expected is None else (expected, stable and actual == expected)


def _percentile(ordered: tuple[float, ...], percentile: float) -> float:
    if not ordered:
        raise ValueError("Timing percentile requires at least one sample")
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _array_digest(values: NDArray[np.float32]) -> str:
    return hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()


def _combined_digest(*values: NDArray[np.float32]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(np.ascontiguousarray(value).tobytes())
    return digest.hexdigest()


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


def _validate_configuration(*, warmup_runs: int, measured_runs: int) -> None:
    if warmup_runs < 1:
        raise ValueError("warmup_runs must be at least 1")
    if measured_runs < 1:
        raise ValueError("measured_runs must be at least 1")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--warmup-runs", type=int, default=DEFAULT_WARMUP_RUNS)
    parser.add_argument("--measured-runs", type=int, default=DEFAULT_MEASURED_RUNS)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_benchmarks(
        output_path=args.output,
        warmup_runs=args.warmup_runs,
        measured_runs=args.measured_runs,
    )
    summary = ", ".join(
        f"{item.algorithm} median={item.timing.median_ms:.3f}ms p95={item.timing.p95_ms:.3f}ms"
        for item in report.benchmarks
    )
    print(f"Synesthesia 500x500 microbenchmarks: {summary}; passed={report.passed}")
    print(args.output.resolve())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
