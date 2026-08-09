"""Measure the canonical graph in the real Qt UI with a spawned Phase 4 engine."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from multiprocessing import freeze_support
from pathlib import Path
from uuid import UUID

import numpy as np
from PySide6.QtCore import QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.application import create_application
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import EngineConnectionState, EngineMetrics, SourceStatus
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import load_graph, save_graph
from synesthesia_machine.runtime import ProcessEngineClient
from synesthesia_machine.ui.main_window import MainWindow
from tools.environment_report import EnvironmentReport, collect_environment_report
from tools.generate_test_video import generate_hue_test_video

CANONICAL_SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
CANONICAL_AUDIO_ID = UUID("30000000-0000-0000-0000-000000000008")
DEFAULT_GRAPH_PATH = Path("examples/phase3/hue_chord.synmachine.json")
DEFAULT_OUTPUT_PATH = Path("docs/phase-4-performance.json")
DEFAULT_SCREENSHOT_PATH = Path("docs/phase-4-ui.png")
DEFAULT_WARMUP_S = 5.0
DEFAULT_MEASUREMENT_S = 30.0


@dataclass(frozen=True, slots=True)
class MetricSample:
    elapsed_s: float
    processed_ticks: int
    processed_fps: float
    p95_node_time_ms: float
    dropped_before_processing: int
    memory_bytes: int


@dataclass(frozen=True, slots=True)
class EngineResult:
    state: str
    processed_ticks: int
    processed_fps: float
    p95_node_time_ms: float
    dropped_before_processing: int
    skipped_by_selection: int
    final_memory_bytes: int
    peak_sampled_memory_bytes: int


@dataclass(frozen=True, slots=True)
class SourceResult:
    state: str
    width: int | None
    height: int | None
    duration_s: float | None
    processed_index: int
    dropped_before_processing: int
    warnings: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class UiResponsivenessResult:
    target_interval_ms: float
    callback_count: int
    expected_callback_count: int
    callback_ratio: float
    p50_interval_ms: float
    p95_interval_ms: float
    maximum_interval_ms: float
    responsive: bool
    criterion: str
    image_preview_visible: bool
    note_preview_visible: bool
    status_text: str


@dataclass(frozen=True, slots=True)
class SourceFixture:
    generated_width: int
    generated_height: int
    generated_fps: int
    generated_frame_count: int
    generated_duration_s: float
    codec: str


@dataclass(frozen=True, slots=True)
class ProcessResult:
    parent_process_id: int
    child_process_id: int
    separate_process: bool
    connection_state: str


@dataclass(frozen=True, slots=True)
class BenchmarkMethodology:
    scope: str
    warmup_s: float
    measurement_s: float
    run_count: int
    preview_policy: str
    windows_power_mode: str | None
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Phase4PerformanceReport:
    generated_at_utc: str
    git_commit: str | None
    canonical_graph_path: str
    methodology: BenchmarkMethodology
    run_duration_observed_s: float
    qt_platform: str
    environment: EnvironmentReport
    process: ProcessResult
    source_fixture: SourceFixture
    engine: EngineResult
    source: SourceResult
    ui: UiResponsivenessResult
    screenshot_path: str
    samples: tuple[MetricSample, ...]


def _metric_sample(metrics: EngineMetrics, elapsed_s: float) -> MetricSample:
    return MetricSample(
        elapsed_s=round(elapsed_s, 3),
        processed_ticks=metrics.processed_ticks,
        processed_fps=metrics.processed_fps,
        p95_node_time_ms=metrics.p95_node_time_ms,
        dropped_before_processing=metrics.dropped_before_processing,
        memory_bytes=metrics.memory_bytes,
    )


def _percentile_ms(intervals_ns: tuple[int, ...], percentile: float) -> float:
    if not intervals_ns:
        return 0.0
    values = np.asarray(intervals_ns, dtype=np.float64) / 1_000_000.0
    return float(np.percentile(values, percentile))


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _windows_power_mode() -> str | None:
    result = subprocess.run(
        ["powercfg", "/getactivescheme"],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


class _PerformanceRun(QObject):
    def __init__(
        self,
        application: QApplication,
        window: MainWindow,
        client: ProcessEngineClient,
        *,
        canonical_graph_path: Path,
        output_path: Path,
        screenshot_path: Path,
        warmup_s: float,
        measurement_s: float,
        heartbeat_interval_ms: int,
        source_fixture: SourceFixture,
        environment: EnvironmentReport,
    ) -> None:
        super().__init__(window)
        self._application = application
        self._window = window
        self._client = client
        self._canonical_graph_path = canonical_graph_path
        self._output_path = output_path
        self._screenshot_path = screenshot_path
        self._warmup_s = warmup_s
        self._measurement_s = measurement_s
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._source_fixture = source_fixture
        self._environment = environment
        self._started_ns: int | None = None
        self._last_heartbeat_ns: int | None = None
        self._heartbeat_intervals_ns: list[int] = []
        self._samples: list[MetricSample] = []
        self.error: str | None = None
        self.report: Phase4PerformanceReport | None = None

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._heartbeat_timer.setInterval(heartbeat_interval_ms)
        self._heartbeat_timer.timeout.connect(self._record_heartbeat)

        self._sample_timer = QTimer(self)
        self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sample_timer.setInterval(1_000)
        self._sample_timer.timeout.connect(self._record_sample)

        self._warmup_timer = QTimer(self)
        self._warmup_timer.setSingleShot(True)
        self._warmup_timer.timeout.connect(self._begin_measurement)

        self._finish_timer = QTimer(self)
        self._finish_timer.setSingleShot(True)
        self._finish_timer.timeout.connect(self._finish)

    def start(self) -> None:
        try:
            status = self._client.source_status(CANONICAL_SOURCE_ID)[0]
            if status.last_error is not None:
                raise RuntimeError(status.last_error)
            self._window.play()
            self._warmup_timer.start(round(self._warmup_s * 1_000.0))
        except Exception as error:  # CLI boundary records Qt callback failures for the caller.
            self._abort(error)

    def _begin_measurement(self) -> None:
        self._started_ns = time.perf_counter_ns()
        self._last_heartbeat_ns = self._started_ns
        self._heartbeat_intervals_ns.clear()
        self._samples.clear()
        self._heartbeat_timer.start()
        self._sample_timer.start()
        self._finish_timer.start(round(self._measurement_s * 1_000.0))

    def _record_heartbeat(self) -> None:
        now_ns = time.perf_counter_ns()
        if self._last_heartbeat_ns is not None:
            self._heartbeat_intervals_ns.append(now_ns - self._last_heartbeat_ns)
        self._last_heartbeat_ns = now_ns

    def _record_sample(self) -> None:
        try:
            started_ns = self._require_started_ns()
            elapsed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000.0
            self._samples.append(_metric_sample(self._client.metrics(), elapsed_s))
        except Exception as error:  # CLI boundary records Qt callback failures for the caller.
            self._abort(error)

    def _finish(self) -> None:
        self._heartbeat_timer.stop()
        self._sample_timer.stop()
        try:
            started_ns = self._require_started_ns()
            elapsed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000.0
            metrics = self._client.metrics()
            source = self._client.source_status(CANONICAL_SOURCE_ID)[0]
            self._samples.append(_metric_sample(metrics, elapsed_s))
            image_previews = self._client.poll_image_previews()
            note_previews = self._client.poll_note_previews()
            for preview in image_previews:
                self._window.image_preview_panel.show_preview(preview)
            for preview in note_previews:
                self._window.note_preview_panel.show_preview(preview)
            self._application.processEvents()
            self._screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._window.grab().save(str(self._screenshot_path), "PNG"):
                raise OSError(f"Could not write screenshot to {self._screenshot_path}")
            self.report = self._create_report(metrics, source, elapsed_s)
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output_path.write_text(
                json.dumps(asdict(self.report), indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except Exception as error:  # CLI boundary records Qt callback failures for the caller.
            self.error = str(error)
        finally:
            self._shutdown()

    def _create_report(
        self,
        metrics: EngineMetrics,
        source: SourceStatus,
        elapsed_s: float,
    ) -> Phase4PerformanceReport:
        intervals = tuple(self._heartbeat_intervals_ns)
        expected_count = max(1, round(elapsed_s * 1_000.0 / self._heartbeat_interval_ms))
        callback_ratio = len(intervals) / expected_count
        p50_ms = _percentile_ms(intervals, 50)
        p95_ms = _percentile_ms(intervals, 95)
        maximum_ms = max(intervals, default=0) / 1_000_000.0
        responsive = callback_ratio >= 0.9 and p95_ms <= 100.0 and maximum_ms <= 250.0
        image_preview = self._window.image_preview_panel.image_widget.latest_preview
        note_preview = self._window.note_preview_panel.note_widget.latest_preview
        peak_memory = max(
            (sample.memory_bytes for sample in self._samples),
            default=metrics.memory_bytes,
        )
        engine_status = self._client.status()
        child_process_id = engine_status.child_process_id
        if (
            engine_status.connection_state is not EngineConnectionState.CONNECTED
            or child_process_id is None
        ):
            raise RuntimeError(f"Spawned engine is not connected: {engine_status!r}")
        return Phase4PerformanceReport(
            generated_at_utc=datetime.now(UTC).isoformat(),
            git_commit=_git_commit(),
            canonical_graph_path=str(self._canonical_graph_path),
            methodology=BenchmarkMethodology(
                scope=(
                    "Process-backed canonical Hue Chord vertical-slice baseline; not the "
                    "Phase 5 reference graph because Gaussian Blur and Canny are not implemented"
                ),
                warmup_s=self._warmup_s,
                measurement_s=self._measurement_s,
                run_count=1,
                preview_policy="Canonical graph includes image and note preview branches",
                windows_power_mode=_windows_power_mode(),
                limitations=(
                    "Source input FPS is recorded as the generated fixture rate, not a separate "
                    "runtime counter because EngineClient does not expose one yet.",
                    "Engine IPC currently exposes aggregate p95 node time, not p50/p99 latency.",
                    "Per-node rolling timings are not yet exposed through EngineClient.",
                    "This diagnostic is one evidence run, not the three-run median release gate.",
                ),
            ),
            run_duration_observed_s=elapsed_s,
            qt_platform=QGuiApplication.platformName(),
            environment=self._environment,
            process=ProcessResult(
                parent_process_id=os.getpid(),
                child_process_id=child_process_id,
                separate_process=child_process_id != os.getpid(),
                connection_state=engine_status.connection_state.value,
            ),
            source_fixture=self._source_fixture,
            engine=EngineResult(
                state=metrics.state.value,
                processed_ticks=metrics.processed_ticks,
                processed_fps=metrics.processed_fps,
                p95_node_time_ms=metrics.p95_node_time_ms,
                dropped_before_processing=metrics.dropped_before_processing,
                skipped_by_selection=metrics.skipped_by_selection,
                final_memory_bytes=metrics.memory_bytes,
                peak_sampled_memory_bytes=peak_memory,
            ),
            source=SourceResult(
                state=source.state.value,
                width=source.width,
                height=source.height,
                duration_s=source.duration_s,
                processed_index=source.processed_index,
                dropped_before_processing=source.dropped_before_processing,
                warnings=source.warnings,
                last_error=source.last_error,
            ),
            ui=UiResponsivenessResult(
                target_interval_ms=float(self._heartbeat_interval_ms),
                callback_count=len(intervals),
                expected_callback_count=expected_count,
                callback_ratio=callback_ratio,
                p50_interval_ms=p50_ms,
                p95_interval_ms=p95_ms,
                maximum_interval_ms=maximum_ms,
                responsive=responsive,
                criterion=(
                    "callback ratio >= 0.90, p95 heartbeat interval <= 100 ms, "
                    "and maximum interval <= 250 ms"
                ),
                image_preview_visible=image_preview is not None,
                note_preview_visible=note_preview is not None,
                status_text=(
                    f"Engine {metrics.state.value} · source {source.state.value} · "
                    f"{metrics.processed_ticks} ticks @ {metrics.processed_fps:.1f} FPS · "
                    f"p95 {metrics.p95_node_time_ms:.2f} ms · "
                    f"drops {metrics.dropped_before_processing} · "
                    f"RAM {metrics.memory_bytes / (1024 * 1024):.1f} MiB"
                ),
            ),
            screenshot_path=str(self._screenshot_path),
            samples=tuple(self._samples),
        )

    def _abort(self, error: Exception) -> None:
        self.error = str(error)
        self._heartbeat_timer.stop()
        self._sample_timer.stop()
        self._warmup_timer.stop()
        self._finish_timer.stop()
        self._shutdown()

    def _shutdown(self) -> None:
        with suppress(IndexError, KeyError, RuntimeError):
            self._window.stop()
        self._window.close()
        self._application.quit()

    def _require_started_ns(self) -> int:
        if self._started_ns is None:
            raise RuntimeError("Performance run has not started")
        return self._started_ns


def _paths(root: Path) -> ApplicationPaths:
    data = root / "application-data"
    paths = ApplicationPaths(data, data / "logs", data / "recovery")
    paths.ensure_exists()
    return paths


def _prepare_runtime_graph(
    canonical_graph_path: Path,
    runtime_root: Path,
    *,
    fps: int,
    source_seconds: int,
) -> tuple[Path, SourceFixture]:
    source_path = runtime_root / "phase4-performance-500x500.mp4"
    frame_count = fps * source_seconds
    generate_hue_test_video(
        source_path,
        width=500,
        height=500,
        frame_count=frame_count,
        fps=fps,
    )
    registry = create_application_registry()
    document = GraphDocument.from_snapshot(load_graph(canonical_graph_path, registry))
    audio = document.node(CANONICAL_AUDIO_ID)
    if audio is None or audio.parameters.get("enabled") is not False:
        raise ValueError("Canonical diagnostic graph must keep Generate Audio disabled")
    document.set_parameter(CANONICAL_SOURCE_ID, "file_path", str(source_path.resolve()))
    document.set_parameter(CANONICAL_SOURCE_ID, "loop", True)
    runtime_graph_path = runtime_root / "phase4-performance.synmachine.json"
    save_graph(runtime_graph_path, document.snapshot())
    return runtime_graph_path, SourceFixture(
        generated_width=500,
        generated_height=500,
        generated_fps=fps,
        generated_frame_count=frame_count,
        generated_duration_s=source_seconds,
        codec="mpeg4/yuv420p",
    )


def run_diagnostic(
    *,
    canonical_graph_path: Path,
    output_path: Path,
    screenshot_path: Path,
    warmup_s: float,
    measurement_s: float,
    fps: int,
    source_seconds: int,
    heartbeat_interval_ms: int,
) -> Phase4PerformanceReport:
    canonical_graph_path = canonical_graph_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    screenshot_path = screenshot_path.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="synmachine-phase4-performance-") as temporary:
        runtime_root = Path(temporary)
        runtime_graph_path, source_fixture = _prepare_runtime_graph(
            canonical_graph_path,
            runtime_root,
            fps=fps,
            source_seconds=source_seconds,
        )
        application = create_application(["synmachine-phase4-performance"])
        registry = create_application_registry()
        client = ProcessEngineClient(
            request_timeout_s=1.5,
            activation_timeout_s=5.0,
            heartbeat_timeout_s=1.5,
            close_timeout_s=0.5,
            crash_log_path=runtime_root / "engine-crash.log",
        )
        window = MainWindow(
            registry,
            _paths(runtime_root),
            client,
            settings=QSettings(
                str(runtime_root / "settings.ini"),
                QSettings.Format.IniFormat,
            ),
            offer_recovery=False,
        )
        window.resize(1600, 950)
        window.show()
        if not window.open_path(runtime_graph_path):
            client.close()
            raise RuntimeError("Could not open the temporary Phase 4 diagnostic graph")
        window.image_preview_dock.show()
        window.note_preview_dock.show()
        window.view.frame_all()
        application.processEvents()
        controller = _PerformanceRun(
            application,
            window,
            client,
            canonical_graph_path=canonical_graph_path,
            output_path=output_path,
            screenshot_path=screenshot_path,
            warmup_s=warmup_s,
            measurement_s=measurement_s,
            heartbeat_interval_ms=heartbeat_interval_ms,
            source_fixture=source_fixture,
            environment=collect_environment_report(),
        )
        QTimer.singleShot(250, controller.start)
        application.exec()
        if controller.error is not None:
            raise RuntimeError(controller.error)
        if controller.report is None:
            raise RuntimeError("Performance run completed without a report")
        return controller.report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--screenshot", type=Path, default=DEFAULT_SCREENSHOT_PATH)
    parser.add_argument("--warmup-seconds", type=float, default=DEFAULT_WARMUP_S)
    parser.add_argument("--measurement-seconds", type=float, default=DEFAULT_MEASUREMENT_S)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--source-seconds", type=int, default=10)
    parser.add_argument("--heartbeat-interval-ms", type=int, default=50)
    return parser


def main() -> int:
    freeze_support()
    parser = _parser()
    args = parser.parse_args()
    if args.warmup_seconds < 0 or args.measurement_seconds < 30.0:
        parser.error("--warmup-seconds must be non-negative and --measurement-seconds at least 30")
    if args.fps <= 0 or args.source_seconds <= 0 or args.heartbeat_interval_ms <= 0:
        parser.error("--fps, --source-seconds, and --heartbeat-interval-ms must be positive")
    report = run_diagnostic(
        canonical_graph_path=args.graph,
        output_path=args.output,
        screenshot_path=args.screenshot,
        warmup_s=args.warmup_seconds,
        measurement_s=args.measurement_seconds,
        fps=args.fps,
        source_seconds=args.source_seconds,
        heartbeat_interval_ms=args.heartbeat_interval_ms,
    )
    print(
        f"Phase 4 process diagnostic: {report.engine.processed_fps:.2f} processed FPS, "
        f"p95 node {report.engine.p95_node_time_ms:.3f} ms, "
        f"drops {report.engine.dropped_before_processing}, "
        f"UI responsive={report.ui.responsive}, child PID={report.process.child_process_id}"
    )
    print(args.output.resolve())
    print(args.screenshot.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
