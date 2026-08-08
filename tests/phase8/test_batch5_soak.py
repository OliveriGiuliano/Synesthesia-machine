"""Phase 8 batch 5 accelerated soak and MIDI overload acceptance."""

from __future__ import annotations

import json
from pathlib import Path

from tools.phase8_soak import run_phase8_soak


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
