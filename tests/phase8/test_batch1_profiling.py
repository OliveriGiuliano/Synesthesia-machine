"""Phase 8 batch 1 bounded profiling aggregation and zero-hook scheduler path."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.runtime import ProcessEngineClient, RuntimeProfiler, Scheduler
from tests.phase1.helpers import frame_context

NODE_ID = UUID("81000000-0000-0000-0000-000000000001")


def test_runtime_profiler_aggregates_accuracy_errors_outputs_and_bounded_window() -> None:
    profiler = RuntimeProfiler(window_capacity=3, ema_alpha=0.5)
    context = FrameContext(NODE_ID, 1, 0, 0.0, 0, None, False)
    data = np.zeros((2, 3, 3), dtype=np.float32)
    data.flags.writeable = False
    image = ImageFrame(
        data,
        ColorSpace.LINEAR_RGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(NODE_ID, "synthetic"),
    )
    for duration_ms in (1, 2, 3, 4):
        profiler.record(
            NODE_ID,
            duration_ms * 1_000_000,
            {"image": image},
            failed=duration_ms == 3,
        )

    profile = profiler.profiles()[0]
    assert profile.invocation_count == 4
    assert profile.error_count == 1
    assert profile.window_size == 3
    assert profile.last_duration_ms == 4.0
    assert profile.ema_duration_ms == 3.125
    assert profile.p50_duration_ms == 3.0
    assert profile.p95_duration_ms == 3.9
    assert profile.max_duration_ms == 4.0
    assert profile.output_summary == "image=IMAGE 3x2x3 float32"
    assert profile.output_bytes == data.nbytes


def test_scheduler_without_hooks_does_not_read_timing_clock(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    registry = create_utility_registry()
    document = GraphDocument()
    document.add_node("synmachine.utility.number", node_id=NODE_ID)
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    calls: list[int] = []
    monkeypatch.setattr(
        "synesthesia_machine.runtime.scheduler.time.perf_counter_ns",
        lambda: calls.append(1) or 10,
    )

    Scheduler(plan).execute_tick(frame_context(clock_id=NODE_ID))

    assert calls == []


def test_process_profiler_query_and_reset_round_trip(tmp_path: Path) -> None:
    client = ProcessEngineClient(
        request_timeout_s=1.5,
        close_timeout_s=0.5,
        crash_log_path=tmp_path / "engine.log",
    )
    try:
        assert client.node_profiles() == ()
        client.reset_profiling()
        assert client.node_profiles() == ()
    finally:
        client.close()
