"""Safety checks for the explicitly opt-in real MIDI evidence command."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.midi_hardware_evidence import run_evidence

from synesthesia_machine.contracts import MidiNoteKey
from synesthesia_machine.midi import MidiMessage, MockMidiOutputBackend

TARGET = "Exact Authorized Target"


def test_evidence_tool_uses_exact_port_off_panic_and_close(tmp_path: Path) -> None:
    backend = MockMidiOutputBackend((TARGET, "Never Substitute"))
    output = tmp_path / "midi-evidence.json"

    report = run_evidence(
        port_name=TARGET,
        output_path=output,
        hold_s=0.0,
        backend=backend,
        sleep=lambda duration_s: None,
    )

    assert backend.opened_names == [TARGET]
    assert len(backend.opened_ports) == 1
    port = backend.opened_ports[0]
    key = MidiNoteKey(0, 60)
    assert port.sent == [
        MidiMessage.all_notes_off(0),
        MidiMessage.note_on(key, 32),
        MidiMessage.note_off(key),
        MidiMessage.all_notes_off(0),
    ]
    assert port.close_count == 1
    assert report.exact_selected_port == TARGET
    assert report.enumerated_before_open == (TARGET, "Never Substitute")
    assert report.connected_status.active_note_count == 1
    assert report.explicit_note_off_status.active_note_count == 0
    assert report.panic_completed and report.close_completed
    assert report.closed_status.connection_state == "CLOSED"
    assert output.is_file()


def test_evidence_tool_refuses_missing_exact_name_without_opening(tmp_path: Path) -> None:
    backend = MockMidiOutputBackend(("Other Port",))
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(RuntimeError, match="is unavailable"):
        run_evidence(
            port_name=TARGET,
            output_path=output,
            hold_s=0.0,
            backend=backend,
            sleep=lambda duration_s: None,
        )

    assert backend.opened_names == []
    assert backend.opened_ports == []
    assert not output.exists()
