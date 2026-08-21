"""Run an accelerated 60-minute-equivalent memory/profiler/MIDI safety soak."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from synesthesia_machine.contracts import FrameContext, MidiNoteKey, MidiStateFrame
from synesthesia_machine.midi import (
    MidiMessage,
    MidiMessageType,
    MidiOutputConfiguration,
    MidiOutputService,
    MockMidiOutputBackend,
    MockMidiOutputPort,
)
from synesthesia_machine.runtime import RuntimeProfiler
from tools.hold_image_soak import run_soak

DEFAULT_OUTPUT = Path("docs/evidence/runtime-soak.json")
DEFAULT_TICKS = 60 * 60 * 60
FPS = 60
PROFILE_NODE_ID = UUID("88000000-0000-0000-0000-000000000001")
MIDI_CLOCK_ID = UUID("88000000-0000-0000-0000-000000000002")
MIDI_SOURCE_ID = UUID("88000000-0000-0000-0000-000000000003")
MIDI_PORT_NAME = "Runtime Soak Mock Loopback"


@dataclass(frozen=True, slots=True)
class MemorySoakResult:
    ticks: int
    equivalent_minutes: float
    wall_clock_s: float
    scheduler_errors: int
    post_warmup_growth_bytes: int
    post_warmup_span_bytes: int
    peak_retained_frames: int
    final_retained_frames: int
    bounded: bool


@dataclass(frozen=True, slots=True)
class ProfilerSoakResult:
    invocations: int
    rolling_window_size: int
    rolling_window_capacity: int
    bounded: bool


@dataclass(frozen=True, slots=True)
class MidiOverloadResult:
    published_state_count: int
    dropped_state_updates: int
    active_notes_after_latest_state: int
    active_notes_after_panic: int
    active_notes_after_close: int
    no_stuck_notes: bool


@dataclass(frozen=True, slots=True)
class RuntimeSoakReport:
    generated_at_utc: str
    git_commit: str | None
    methodology: dict[str, object]
    memory: MemorySoakResult
    profiler: ProfilerSoakResult
    midi_overload: MidiOverloadResult
    passed: bool


class _BlockingNoteOnPort(MockMidiOutputPort):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._blocked = False

    def send(self, message: MidiMessage) -> None:
        if message.message_type is MidiMessageType.NOTE_ON and not self._blocked:
            self._blocked = True
            self.entered.set()
            if not self.release.wait(5.0):
                raise TimeoutError("Runtime soak MIDI overload did not release its sender")
        super().send(message)


class _BlockingMidiBackend(MockMidiOutputBackend):
    def __init__(self) -> None:
        super().__init__((MIDI_PORT_NAME,))
        self.blocking_port: _BlockingNoteOnPort | None = None

    def open_output(self, name: str) -> MockMidiOutputPort:
        if name != MIDI_PORT_NAME:
            raise RuntimeError(f"unexpected mock MIDI output: {name}")
        port = _BlockingNoteOnPort(name)
        self.blocking_port = port
        self.opened_names.append(name)
        self.opened_ports.append(port)
        return port


def run_runtime_soak(*, output_path: Path, total_ticks: int = DEFAULT_TICKS) -> RuntimeSoakReport:
    if total_ticks < 120:
        raise ValueError("Runtime soak requires at least 120 ticks")
    warmup_ticks = min(6_000, total_ticks // 3)
    sample_interval = max(1, total_ticks // 12)
    with tempfile.TemporaryDirectory(prefix="synmachine-runtime-soak-") as temporary:
        memory_report = run_soak(
            output_path=Path(temporary) / "memory.json",
            total_ticks=total_ticks,
            fps=FPS,
            loop_frames=600,
            delay_frames=8,
            memory_limit_mb=16,
            width=64,
            height=64,
            sample_interval_ticks=sample_interval,
            warmup_ticks=warmup_ticks,
            rss_growth_limit_mb=32,
        )
    profiler = RuntimeProfiler(window_capacity=240)
    for _ in range(total_ticks):
        profiler.record(PROFILE_NODE_ID, 1_000, {}, failed=False)
    profile = profiler.profiles()[0]
    profiler_result = ProfilerSoakResult(
        invocations=profile.invocation_count,
        rolling_window_size=profile.window_size,
        rolling_window_capacity=240,
        bounded=(
            profile.window_size == min(240, total_ticks) and profile.invocation_count == total_ticks
        ),
    )
    midi_result = _midi_overload_evidence()
    memory_result = MemorySoakResult(
        ticks=total_ticks,
        equivalent_minutes=total_ticks / FPS / 60.0,
        wall_clock_s=memory_report.wall_clock_duration_s,
        scheduler_errors=memory_report.scheduler_error_count,
        post_warmup_growth_bytes=memory_report.process_memory.post_warmup_growth_bytes,
        post_warmup_span_bytes=memory_report.process_memory.post_warmup_span_bytes,
        peak_retained_frames=memory_report.hold_image.peak_retained_frame_count,
        final_retained_frames=memory_report.hold_image.final_retained_frame_count,
        bounded=memory_report.passed,
    )
    passed = memory_result.bounded and profiler_result.bounded and midi_result.no_stuck_notes
    report = RuntimeSoakReport(
        generated_at_utc=datetime.now(UTC).isoformat(),
        git_commit=_git_commit(),
        methodology={
            "accelerated": True,
            "target_fps": FPS,
            "equivalent_duration_minutes": total_ticks / FPS / 60.0,
            "memory_scope": "Production scheduler, looping source resets, bounded Hold Image",
            "profiler_scope": "Bounded rolling window under one record per equivalent frame",
            "midi_scope": "Latest-state mailbox overload followed by panic and close",
        },
        memory=memory_result,
        profiler=profiler_result,
        midi_overload=midi_result,
        passed=passed,
    )
    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def _midi_overload_evidence() -> MidiOverloadResult:
    backend = _BlockingMidiBackend()
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    configuration = MidiOutputConfiguration(output_port=MIDI_PORT_NAME)
    published_count = 20
    try:
        if not service.wait_until_idle(2.0):
            raise TimeoutError("MIDI service did not initialize")
        service.publish(_midi_frame(60, 1), configuration)
        port = _require_blocking_port(backend)
        if not port.entered.wait(2.0):
            raise TimeoutError("MIDI sender did not enter the deliberate overload")
        for index in range(2, published_count + 1):
            service.publish(_midi_frame(60 + index % 12, index), configuration)
        dropped = service.status().dropped_state_updates
        port.release.set()
        if not service.wait_until_idle(5.0):
            raise TimeoutError("MIDI service did not drain its latest state")
        latest_active = service.status().active_note_count
        service.panic(5.0)
        after_panic = service.status().active_note_count
    finally:
        if backend.blocking_port is not None:
            backend.blocking_port.release.set()
        service.close()
    after_close = service.status().active_note_count
    return MidiOverloadResult(
        published_state_count=published_count,
        dropped_state_updates=dropped,
        active_notes_after_latest_state=latest_active,
        active_notes_after_panic=after_panic,
        active_notes_after_close=after_close,
        no_stuck_notes=(
            dropped > 0 and latest_active == 1 and after_panic == 0 and after_close == 0
        ),
    )


def _midi_frame(note: int, tick_index: int) -> MidiStateFrame:
    return MidiStateFrame(
        {MidiNoteKey(0, note): 96},
        FrameContext(MIDI_CLOCK_ID, tick_index, None, 0.0, tick_index, None, False),
        MIDI_SOURCE_ID,
    )


def _require_blocking_port(backend: _BlockingMidiBackend) -> _BlockingNoteOnPort:
    for _ in range(200):
        port = backend.blocking_port
        if port is not None:
            return port
        time.sleep(0.005)
    raise TimeoutError("mock MIDI output did not open")


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    args = parser.parse_args()
    report = run_runtime_soak(output_path=args.output, total_ticks=args.ticks)
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
