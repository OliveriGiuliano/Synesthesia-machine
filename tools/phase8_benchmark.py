"""Benchmark the Phase 8 500x500/60 FPS reference graph through the real UI and child engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from multiprocessing import freeze_support
from pathlib import Path
from uuid import UUID

import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.application import create_application
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import NodeProfile
from synesthesia_machine.diagnostics import collect_hardware_snapshot, dependency_versions
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import save_graph
from synesthesia_machine.runtime import ProcessEngineClient
from synesthesia_machine.runtime.engine_server import MAX_OPENCV_THREADS
from synesthesia_machine.ui.main_window import MainWindow
from tools.generate_test_video import generate_hue_test_video

SOURCE_ID = UUID("80000000-0000-0000-0000-000000000001")
RESIZE_ID = UUID("80000000-0000-0000-0000-000000000002")
BLUR_ID = UUID("80000000-0000-0000-0000-000000000003")
HSV_ID = UUID("80000000-0000-0000-0000-000000000004")
CHANNELS_ID = UUID("80000000-0000-0000-0000-000000000005")
PITCH_ID = UUID("80000000-0000-0000-0000-000000000006")
NOTE_VISUALIZER_ID = UUID("80000000-0000-0000-0000-000000000007")
CANNY_ID = UUID("80000000-0000-0000-0000-000000000008")
CHANNEL_DISPLAY_ID = UUID("80000000-0000-0000-0000-000000000009")

DEFAULT_OUTPUT = Path("docs/phase-8-baseline.json")
DEFAULT_REFERENCE_GRAPH = Path("examples/phase8/reference_500x500.synmachine.json")
DEFAULT_WARMUP_S = 5.0
DEFAULT_MEASUREMENT_S = 30.0
DEFAULT_RUNS = 3
FRAME_BUDGET_MS = 1000.0 / 60.0


@dataclass(frozen=True, slots=True)
class PerNodeResult:
    node_id: str
    node_name: str
    invocations: int
    errors: int
    window_size: int
    last_ms: float
    ema_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    output_summary: str
    output_bytes: int


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    run_index: int
    observed_measurement_s: float
    input_fps: float
    processed_fps: float
    preview_fps: float
    processed_ticks: int
    dropped_before_processing: int
    skipped_by_selection: int
    process_every_nth_frame: int
    graph_latency_window_size: int
    graph_p50_ms: float
    graph_p95_ms: float
    graph_p99_ms: float
    graph_max_ms: float
    frame_age_ms: float
    end_to_end_processing_latency_ms: float
    mailbox_occupancy: int
    mailbox_capacity: int
    engine_cpu_percent: float
    engine_rss_bytes: int
    peak_sampled_engine_rss_bytes: int
    ui_heartbeat_p50_ms: float
    ui_heartbeat_p95_ms: float
    ui_heartbeat_max_ms: float
    ui_responsive: bool
    source_state: str
    source_width: int | None
    source_height: int | None
    per_node: tuple[PerNodeResult, ...]


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    median_processed_fps: float
    median_graph_p95_ms: float
    all_runs_ui_responsive: bool
    total_dropped_before_processing: int
    processed_fps_target: float
    graph_p95_target_ms: float
    passed: bool


@dataclass(frozen=True, slots=True)
class Phase8BenchmarkReport:
    generated_at_utc: str
    label: str
    git_commit: str | None
    methodology: dict[str, object]
    reference_graph: dict[str, object]
    environment: dict[str, object]
    release_gate: ReleaseGate
    runs: tuple[BenchmarkRun, ...]


def create_reference_document(media_path: str) -> GraphDocument:
    """Create the deterministic two-branch Phase 8 reference graph."""

    document = GraphDocument(document_id=UUID("80000000-0000-0000-0000-000000000000"))
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={
            "file_path": media_path,
            "process_every_nth_frame": 1,
            "loop": True,
            "stream_index": 0,
        },
        position=(0.0, 100.0),
    )
    document.add_node(
        "synmachine.image.resize",
        node_id=RESIZE_ID,
        parameters={
            "width": 500,
            "height": 500,
            "preserve_aspect": False,
            "fit_mode": "STRETCH",
            "interpolation": "AUTO",
        },
        position=(300.0, 100.0),
    )
    document.add_node(
        "synmachine.image.gaussian_blur",
        node_id=BLUR_ID,
        parameters={
            "kernel_width": 5,
            "kernel_height": 5,
            "sigma_x": 0.0,
            "sigma_y": 0.0,
            "border_mode": "REFLECT_101",
        },
        position=(600.0, 100.0),
    )
    document.add_node(
        "synmachine.image.change_colour_space",
        node_id=HSV_ID,
        parameters={"target_colour_space": "HSV"},
        position=(900.0, 100.0),
    )
    document.add_node(
        "synmachine.image.separate_channels",
        node_id=CHANNELS_ID,
        position=(1200.0, 0.0),
    )
    document.add_node(
        "synmachine.synesthesia.channel_to_pitch",
        node_id=PITCH_ID,
        position=(1500.0, 0.0),
    )
    document.add_node(
        "synmachine.visualization.note_visualizer",
        node_id=NOTE_VISUALIZER_ID,
        position=(1800.0, 0.0),
    )
    document.add_node(
        "synmachine.image.canny",
        node_id=CANNY_ID,
        position=(1200.0, 350.0),
    )
    document.add_node(
        "synmachine.visualization.channel_display",
        node_id=CHANNEL_DISPLAY_ID,
        parameters={
            "preview_fps": 30,
            "max_dimension": 800,
            "fit_mode": "CONTAIN",
            "value_display_mode": "NOMINAL_RANGE",
            "show_histogram": False,
        },
        position=(1500.0, 350.0),
    )
    connections = (
        (SOURCE_ID, "image", RESIZE_ID, "image"),
        (RESIZE_ID, "image", BLUR_ID, "image"),
        (BLUR_ID, "image", HSV_ID, "image"),
        (HSV_ID, "image", CHANNELS_ID, "image"),
        (CHANNELS_ID, "channel_1", PITCH_ID, "value"),
        (PITCH_ID, "midi", NOTE_VISUALIZER_ID, "midi"),
        (HSV_ID, "image", CANNY_ID, "image"),
        (CANNY_ID, "channel", CHANNEL_DISPLAY_ID, "channel"),
    )
    for index, (source, source_port, destination, destination_port) in enumerate(
        connections, start=1
    ):
        document.add_connection(
            source,
            source_port,
            destination,
            destination_port,
            connection_id=UUID(f"80000000-0000-0000-0001-{index:012d}"),
        )
    return document


def write_reference_graph(path: Path = DEFAULT_REFERENCE_GRAPH) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    save_graph(path, create_reference_document("phase8-synthetic-500x500-60fps.mp4").snapshot())
    return path


def aggregate_release_gate(runs: tuple[BenchmarkRun, ...]) -> ReleaseGate:
    if not runs:
        raise ValueError("at least one benchmark run is required")
    median_fps = statistics.median(run.processed_fps for run in runs)
    median_p95 = statistics.median(run.graph_p95_ms for run in runs)
    responsive = all(run.ui_responsive for run in runs)
    drops = sum(run.dropped_before_processing for run in runs)
    return ReleaseGate(
        median_processed_fps=median_fps,
        median_graph_p95_ms=median_p95,
        all_runs_ui_responsive=responsive,
        total_dropped_before_processing=drops,
        processed_fps_target=60.0,
        graph_p95_target_ms=FRAME_BUDGET_MS,
        passed=median_fps >= 60.0 and median_p95 < FRAME_BUDGET_MS and responsive,
    )


def run_benchmark(
    *,
    output_path: Path,
    label: str,
    warmup_s: float,
    measurement_s: float,
    run_count: int,
    source_fps: int = 60,
) -> Phase8BenchmarkReport:
    if warmup_s < 0.0 or measurement_s <= 0.0 or run_count < 1:
        raise ValueError("warmup must be non-negative; measurement and run count must be positive")
    application = create_application(["synmachine-phase8-benchmark"])
    application.setQuitOnLastWindowClosed(False)
    hardware = collect_hardware_snapshot()
    with tempfile.TemporaryDirectory(prefix="synmachine-phase8-benchmark-") as temporary:
        root = Path(temporary)
        source_seconds = max(10, int(np.ceil(warmup_s + measurement_s)) + 5)
        frame_count = source_seconds * source_fps
        source_path = root / "synthetic-500x500-60fps.mp4"
        generate_hue_test_video(
            source_path,
            width=500,
            height=500,
            frame_count=frame_count,
            fps=source_fps,
        )
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        graph_path = root / "reference.synmachine.json"
        save_graph(graph_path, create_reference_document(str(source_path)).snapshot())
        results = tuple(
            _run_once(
                application,
                graph_path,
                root / f"run-{run_index}",
                run_index=run_index,
                warmup_s=warmup_s,
                measurement_s=measurement_s,
                input_fps=float(source_fps),
            )
            for run_index in range(1, run_count + 1)
        )
    report = Phase8BenchmarkReport(
        generated_at_utc=datetime.now(UTC).isoformat(),
        label=label,
        git_commit=_git_commit(),
        methodology={
            "scope": "Real Qt main window with spawned child engine and visible preview branch",
            "warmup_s": warmup_s,
            "measurement_s": measurement_s,
            "run_count": run_count,
            "aggregation": "median across runs for release FPS and graph p95",
            "latency_window": "last 240 graph executions, reset after warmup",
            "ui_heartbeat_interval_ms": 16,
            "ui_responsiveness_criterion": "p95 <= 50 ms and max <= 250 ms",
        },
        reference_graph={
            "resolution": [500, 500],
            "source_fps": source_fps,
            "source_frame_count": frame_count,
            "source_sha256": source_hash,
            "process_every_nth_frame": 1,
            "node_count": 9,
            "connection_count": 8,
            "preview_policy": "Note Visualizer plus 30 FPS Canny Channel Display",
            "canny_endpoint_note": (
                "The implemented Canny node outputs CHANNEL, so the reference uses the typed "
                "Channel Display equivalent of the architecture packet's Display Image endpoint."
            ),
        },
        environment={
            "hardware": asdict(hardware),
            "dependencies": dependency_versions(),
            "opencv_thread_policy": {
                "maximum_threads": MAX_OPENCV_THREADS,
                "selected_threads": max(
                    1, min(MAX_OPENCV_THREADS, hardware.cpu_physical or hardware.cpu_logical or 1)
                ),
            },
            "qt_platform": QGuiApplication.platformName(),
            "windows_power_scheme": _windows_power_scheme(),
            "parent_process_id": os.getpid(),
        },
        release_gate=aggregate_release_gate(results),
        runs=results,
    )
    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def _run_once(
    application: QApplication,
    graph_path: Path,
    run_root: Path,
    *,
    run_index: int,
    warmup_s: float,
    measurement_s: float,
    input_fps: float,
) -> BenchmarkRun:
    paths = ApplicationPaths(run_root, run_root / "logs", run_root / "recovery")
    paths.ensure_exists()
    registry = create_application_registry()
    client = ProcessEngineClient(
        request_timeout_s=2.0,
        activation_timeout_s=8.0,
        heartbeat_timeout_s=2.0,
        close_timeout_s=1.0,
        crash_log_path=paths.logs / "engine-crash.log",
    )
    window = MainWindow(
        registry,
        paths,
        client,
        settings=QSettings(str(run_root / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    try:
        window.resize(1500, 900)
        window.show()
        window.preview_dock.show()
        if not window.open_path(graph_path):
            raise RuntimeError("could not open the Phase 8 reference graph")
        if not _wait_until(application, lambda: _source_is_available(client), 10.0):
            raise TimeoutError("reference source did not activate")
        client.play(SOURCE_ID)
        _pump_for(application, warmup_s)
        client.set_profiling_enabled(True)
        client.reset_profiling()
        started_metrics = client.metrics()
        started_ns = time.perf_counter_ns()
        heartbeat_ns: list[int] = []
        last_heartbeat_ns: int | None = None

        def record_heartbeat() -> None:
            nonlocal last_heartbeat_ns
            now_ns = time.perf_counter_ns()
            if last_heartbeat_ns is not None:
                heartbeat_ns.append(now_ns - last_heartbeat_ns)
            last_heartbeat_ns = now_ns

        timer = QTimer(window)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(16)
        timer.timeout.connect(record_heartbeat)
        timer.start()
        memory_samples = [started_metrics.memory_bytes]
        next_sample_ns = started_ns
        deadline_ns = started_ns + round(measurement_s * 1_000_000_000)
        while time.perf_counter_ns() < deadline_ns:
            application.processEvents()
            now_ns = time.perf_counter_ns()
            if now_ns >= next_sample_ns:
                memory_samples.append(client.metrics().memory_bytes)
                next_sample_ns = now_ns + 250_000_000
            time.sleep(0.002)
        timer.stop()
        observed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000.0
        final_metrics = client.metrics()
        source = client.source_status(SOURCE_ID)[0]
        profiles = client.node_profiles()
        processed_ticks = final_metrics.processed_ticks - started_metrics.processed_ticks
        names = {node.node_id: node.title for node in window.session.view_model.nodes}
        heartbeat_p50 = _percentile_ms(heartbeat_ns, 50.0)
        heartbeat_p95 = _percentile_ms(heartbeat_ns, 95.0)
        heartbeat_max = max(heartbeat_ns, default=0) / 1_000_000.0
        return BenchmarkRun(
            run_index=run_index,
            observed_measurement_s=observed_s,
            input_fps=input_fps,
            processed_fps=processed_ticks / measurement_s,
            preview_fps=final_metrics.preview_fps,
            processed_ticks=processed_ticks,
            dropped_before_processing=(
                final_metrics.dropped_before_processing - started_metrics.dropped_before_processing
            ),
            skipped_by_selection=(
                final_metrics.skipped_by_selection - started_metrics.skipped_by_selection
            ),
            process_every_nth_frame=1,
            graph_latency_window_size=final_metrics.graph_latency_window_size,
            graph_p50_ms=final_metrics.p50_graph_execution_ms,
            graph_p95_ms=final_metrics.p95_graph_execution_ms,
            graph_p99_ms=final_metrics.p99_graph_execution_ms,
            graph_max_ms=final_metrics.max_graph_execution_ms,
            frame_age_ms=final_metrics.frame_age_ms,
            end_to_end_processing_latency_ms=final_metrics.processing_latency_ms,
            mailbox_occupancy=final_metrics.mailbox_occupancy,
            mailbox_capacity=final_metrics.mailbox_capacity,
            engine_cpu_percent=final_metrics.cpu_percent,
            engine_rss_bytes=final_metrics.memory_bytes,
            peak_sampled_engine_rss_bytes=max(memory_samples),
            ui_heartbeat_p50_ms=heartbeat_p50,
            ui_heartbeat_p95_ms=heartbeat_p95,
            ui_heartbeat_max_ms=heartbeat_max,
            ui_responsive=heartbeat_p95 <= 50.0 and heartbeat_max <= 250.0,
            source_state=source.state.value,
            source_width=source.width,
            source_height=source.height,
            per_node=_per_node_results(profiles, names),
        )
    finally:
        with suppress(KeyError, RuntimeError, TimeoutError):
            client.stop(SOURCE_ID)
        window.close()
        application.processEvents()


def _per_node_results(
    profiles: tuple[NodeProfile, ...], names: dict[UUID, str]
) -> tuple[PerNodeResult, ...]:
    return tuple(
        PerNodeResult(
            node_id=str(profile.node_id),
            node_name=names.get(profile.node_id, str(profile.node_id)[:8]),
            invocations=profile.invocation_count,
            errors=profile.error_count,
            window_size=profile.window_size,
            last_ms=profile.last_duration_ms,
            ema_ms=profile.ema_duration_ms,
            p50_ms=profile.p50_duration_ms,
            p95_ms=profile.p95_duration_ms,
            max_ms=profile.max_duration_ms,
            output_summary=profile.output_summary,
            output_bytes=profile.output_bytes,
        )
        for profile in sorted(profiles, key=lambda item: names.get(item.node_id, str(item.node_id)))
    )


def _source_is_available(client: ProcessEngineClient) -> bool:
    try:
        source = client.source_status(SOURCE_ID)[0]
    except (IndexError, KeyError, RuntimeError, TimeoutError):
        return False
    if source.last_error is not None:
        raise RuntimeError(source.last_error)
    return True


def _wait_until(application: QApplication, predicate: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _pump_for(application: QApplication, duration_s: float) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.002)


def _percentile_ms(values_ns: list[int], percentile: float) -> float:
    if not values_ns:
        return 0.0
    return float(np.percentile(np.asarray(values_ns, dtype=np.float64), percentile) / 1_000_000.0)


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _windows_power_scheme() -> str | None:
    result = subprocess.run(
        ["powercfg", "/getactivescheme"], check=False, capture_output=True, text=True
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--warmup-seconds", type=float, default=DEFAULT_WARMUP_S)
    parser.add_argument("--measurement-seconds", type=float, default=DEFAULT_MEASUREMENT_S)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--write-reference-only", action="store_true")
    args = parser.parse_args()
    if args.write_reference_only:
        print(write_reference_graph())
        return 0
    report = run_benchmark(
        output_path=args.output,
        label=args.label,
        warmup_s=args.warmup_seconds,
        measurement_s=args.measurement_seconds,
        run_count=args.runs,
    )
    print(json.dumps(asdict(report.release_gate), indent=2))
    return 0


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
