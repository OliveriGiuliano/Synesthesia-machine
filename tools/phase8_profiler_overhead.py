"""Measure opt-in per-node profiling overhead on the Phase 8 reference graph."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler
from synesthesia_machine.runtime import RuntimeProfiler, Scheduler
from synesthesia_machine.runtime.execution_plan import ExecutionPlan, PortKey
from tools.phase8_benchmark import SOURCE_ID, create_reference_document

DEFAULT_OUTPUT = Path("docs/phase-8-profiler-overhead.json")


@dataclass(frozen=True, slots=True)
class OverheadSample:
    repetition: int
    profiler_off_ms_per_tick: float
    profiler_on_ms_per_tick: float
    overhead_percent: float


@dataclass(frozen=True, slots=True)
class ProfilerOverheadReport:
    resolution: tuple[int, int]
    measured_ticks_per_mode: int
    repetitions: int
    median_off_ms_per_tick: float
    median_on_ms_per_tick: float
    overhead_percent: float
    target_percent: float
    passed: bool
    samples: tuple[OverheadSample, ...]


def measure_profiler_overhead(
    *, measured_ticks: int = 120, repetitions: int = 3
) -> ProfilerOverheadReport:
    if measured_ticks < 1 or repetitions < 1:
        raise ValueError("measured ticks and repetitions must be positive")
    registry = create_application_registry()
    result = GraphCompiler(registry).compile(create_reference_document("unused.mp4").snapshot())
    if result.plan is None:
        raise RuntimeError(f"reference graph did not compile: {result.report.errors}")
    context = FrameContext(SOURCE_ID, 1, 0, 0.0, 0, None, False)
    data = np.random.default_rng(8).random((500, 500, 3), dtype=np.float32)
    data.flags.writeable = False
    image = ImageFrame(
        data,
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(SOURCE_ID, "generated-profiler-overhead"),
    )
    source_values: dict[PortKey, RuntimeValue] = {
        PortKey(SOURCE_ID, "image"): image,
        PortKey(SOURCE_ID, "processed_index"): 1,
    }
    samples: list[OverheadSample] = []
    for repetition in range(1, repetitions + 1):
        if repetition % 2:
            off = _measure_mode(result.plan, source_values, context, measured_ticks, enabled=False)
            on = _measure_mode(result.plan, source_values, context, measured_ticks, enabled=True)
        else:
            on = _measure_mode(result.plan, source_values, context, measured_ticks, enabled=True)
            off = _measure_mode(result.plan, source_values, context, measured_ticks, enabled=False)
        samples.append(OverheadSample(repetition, off, on, (on / off - 1.0) * 100.0))
    median_off = statistics.median(sample.profiler_off_ms_per_tick for sample in samples)
    median_on = statistics.median(sample.profiler_on_ms_per_tick for sample in samples)
    overhead = statistics.median(sample.overhead_percent for sample in samples)
    return ProfilerOverheadReport(
        resolution=(500, 500),
        measured_ticks_per_mode=measured_ticks,
        repetitions=repetitions,
        median_off_ms_per_tick=median_off,
        median_on_ms_per_tick=median_on,
        overhead_percent=overhead,
        target_percent=3.0,
        passed=overhead < 3.0,
        samples=tuple(samples),
    )


def _measure_mode(
    plan: ExecutionPlan,
    source_values: dict[PortKey, RuntimeValue],
    context: FrameContext,
    tick_count: int,
    *,
    enabled: bool,
) -> float:
    profiler = RuntimeProfiler()
    scheduler = Scheduler(plan, profiling_hook=profiler.record if enabled else None)
    try:
        for _ in range(5):
            scheduler.execute_tick(context, source_values=source_values)
        started_ns = time.perf_counter_ns()
        for _ in range(tick_count):
            scheduler.execute_tick(context, source_values=source_values)
        return (time.perf_counter_ns() - started_ns) / tick_count / 1_000_000.0
    finally:
        scheduler.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    report = measure_profiler_overhead(
        measured_ticks=args.ticks,
        repetitions=args.repetitions,
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
