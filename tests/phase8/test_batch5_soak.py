"""Phase 8 batch 5 accelerated soak and MIDI overload acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from tools.phase8_soak import run_phase8_soak

from synesthesia_machine.contracts import FrameContext


def test_fast_soak_bounds_memory_profiler_window_and_clears_overloaded_midi(
    tmp_path: Path,
) -> None:
    output = tmp_path / "phase-8-soak.json"

    report = run_phase8_soak(output_path=output, total_ticks=120)

    assert report.passed
    assert report.memory.bounded
    assert report.memory.scheduler_errors == 0
    assert report.profiler.invocations == 120
    assert report.profiler.rolling_window_size == 120
    assert report.midi_overload.dropped_state_updates > 0
    assert report.midi_overload.active_notes_after_latest_state == 1
    assert report.midi_overload.active_notes_after_panic == 0
    assert report.midi_overload.active_notes_after_close == 0
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True


def test_frame_context_construction_is_stable_across_a_long_running_session() -> None:
    clock_id = UUID("88000000-0000-0000-0000-000000000010")
    context: FrameContext | None = None

    for tick_index in range(1, 500_001):
        context = FrameContext(
            clock_id,
            tick_index,
            (tick_index - 1) % 600,
            ((tick_index - 1) % 600) / 60.0,
            tick_index,
            None,
            False,
        )

    assert context is not None
    assert context.tick_index == 500_000
