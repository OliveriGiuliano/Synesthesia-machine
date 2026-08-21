"""Run one explicitly confirmed exact-port MIDI lifecycle check and record evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    MidiNoteKey,
    MidiOutputConnectionState,
    MidiStateFrame,
)
from synesthesia_machine.midi import (
    MidiOutputBackend,
    MidiOutputConfiguration,
    MidiOutputService,
    MidiServiceStatus,
    MidoRtMidiBackend,
)

DEFAULT_OUTPUT_PATH = Path("docs/evidence/midi-hardware.json")
CLOCK_ID = UUID("40000000-0000-0000-0000-000000000001")
SOURCE_ID = UUID("40000000-0000-0000-0000-000000000002")


@dataclass(frozen=True, slots=True)
class StatusEvidence:
    connection_state: str
    selected_port: str
    available_ports: tuple[str, ...]
    active_note_count: int
    active_channels: tuple[int, ...]
    dropped_state_updates: int
    last_error: str | None
    sender_thread_id: int | None


@dataclass(frozen=True, slots=True)
class MidiHardwareEvidence:
    generated_at_utc: str
    git_commit: str | None
    backend: str
    exact_selected_port: str
    enumerated_before_open: tuple[str, ...]
    test_note: int
    test_velocity: int
    test_channel_runtime: int
    hold_s: float
    lifecycle: tuple[str, ...]
    connected_status: StatusEvidence
    explicit_note_off_status: StatusEvidence
    panic_status: StatusEvidence
    closed_status: StatusEvidence
    panic_completed: bool
    close_completed: bool
    midi_view_observation: str


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _frame(notes: dict[MidiNoteKey, int], tick_index: int) -> MidiStateFrame:
    return MidiStateFrame(
        notes,
        FrameContext(CLOCK_ID, tick_index, None, 0.0, time.perf_counter_ns(), None, True),
        SOURCE_ID,
    )


def _status_evidence(status: MidiServiceStatus) -> StatusEvidence:
    return StatusEvidence(
        connection_state=status.connection_state.value,
        selected_port=status.selected_port,
        available_ports=status.available_ports,
        active_note_count=status.active_note_count,
        active_channels=status.active_channels,
        dropped_state_updates=status.dropped_state_updates,
        last_error=status.last_error,
        sender_thread_id=status.sender_thread_id,
    )


def _require_idle(service: MidiOutputService, stage: str) -> None:
    if not service.wait_until_idle(2.0):
        raise TimeoutError(f"Timed out waiting for MIDI sender during {stage}")


def run_evidence(
    *,
    port_name: str,
    output_path: Path,
    hold_s: float,
    note: int = 60,
    velocity: int = 32,
    channel: int = 0,
    backend: MidiOutputBackend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> MidiHardwareEvidence:
    if not port_name:
        raise ValueError("An exact non-empty MIDI output name is required")
    if hold_s < 0.0:
        raise ValueError("hold_s must be non-negative")
    real_backend = backend or MidoRtMidiBackend()
    enumerated = tuple(real_backend.output_names())
    if port_name not in enumerated:
        raise RuntimeError(
            f"Exact MIDI output {port_name!r} is unavailable; enumerated outputs: {enumerated!r}"
        )

    service = MidiOutputService(real_backend, refresh_interval_s=3600.0)
    panic_completed = False
    close_completed = False
    try:
        _require_idle(service, "initial exact-port enumeration")
        configuration = MidiOutputConfiguration(output_port=port_name)
        key = MidiNoteKey(channel, note)

        service.publish(_frame({key: velocity}, 1), configuration)
        _require_idle(service, "note-on")
        connected = service.status()
        if (
            connected.connection_state is not MidiOutputConnectionState.CONNECTED
            or connected.selected_port != port_name
            or connected.active_note_count != 1
            or connected.last_error is not None
        ):
            raise RuntimeError(f"MIDI note-on did not reach the exact selected port: {connected!r}")

        sleep(hold_s)
        service.publish(_frame({}, 2), configuration)
        _require_idle(service, "explicit note-off")
        note_off = service.status()
        if note_off.active_note_count != 0 or note_off.last_error is not None:
            raise RuntimeError(f"MIDI explicit note-off did not clear tracked state: {note_off!r}")

        service.panic(2.0)
        panic_completed = True
        _require_idle(service, "CC123 panic")
        panic = service.status()
        if panic.active_note_count != 0 or panic.last_error is not None:
            raise RuntimeError(f"MIDI panic did not leave a clean tracked state: {panic!r}")
    finally:
        if not panic_completed:
            with suppress(Exception):
                service.panic(2.0)
        service.close()
        close_completed = True

    closed = service.status()
    if closed.connection_state is not MidiOutputConnectionState.CLOSED:
        raise RuntimeError(f"MIDI service did not close cleanly: {closed!r}")

    report = MidiHardwareEvidence(
        generated_at_utc=datetime.now(UTC).isoformat(),
        git_commit=_git_commit(),
        backend="mido.backends.rtmidi via MidiOutputService",
        exact_selected_port=port_name,
        enumerated_before_open=enumerated,
        test_note=note,
        test_velocity=velocity,
        test_channel_runtime=channel,
        hold_s=hold_s,
        lifecycle=(
            "enumerate without opening",
            "open exact confirmed port on sender thread",
            "send CC123 open guard",
            "publish desired note state",
            "publish empty state for explicit note-off",
            "panic with CC123",
            "close persistent port",
        ),
        connected_status=_status_evidence(connected),
        explicit_note_off_status=_status_evidence(note_off),
        panic_status=_status_evidence(panic),
        closed_status=_status_evidence(closed),
        panic_completed=panic_completed,
        close_completed=close_completed,
        midi_view_observation=(
            "Not machine-observed. Visual receipt in MIDIView requires separate manual "
            "confirmation."
        ),
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
    parser.add_argument("--port", required=True, help="exact enumerated output name")
    parser.add_argument(
        "--confirm-exact-port",
        required=True,
        help="must exactly repeat --port before any port can be opened",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--hold-seconds", type=float, default=1.0)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.port != args.confirm_exact_port:
        parser.error("--confirm-exact-port must exactly equal --port")
    if args.hold_seconds < 0.0:
        parser.error("--hold-seconds must be non-negative")
    report = run_evidence(
        port_name=args.port,
        output_path=args.output,
        hold_s=args.hold_seconds,
    )
    print(
        f"Exact-port MIDI lifecycle completed on {report.exact_selected_port!r}; "
        f"panic={report.panic_completed}, close={report.close_completed}"
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
