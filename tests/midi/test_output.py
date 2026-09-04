"""Deterministic Send MIDI diffing, lifecycle, status, and IPC safety matrix."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

import mido
import pytest
from tests.support.graph_factories import frame_context, make_definition

from synesthesia_machine.contracts import (
    MidiNoteKey,
    MidiOutputConnectionState,
    MidiOutputStatus,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
)
from synesthesia_machine.contracts.engine_messages import (
    MidiOutputStatusResponse,
    QueryMidiOutputStatus,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.midi import (
    MidiMessage,
    MidiMessageType,
    MidiOutputConfiguration,
    MidiOutputService,
    MidiServiceStatus,
    MidoRtMidiBackend,
    MockMidiOutputBackend,
    MockMidiOutputPort,
    VelocityUpdatePolicy,
)
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    NodeDefinition,
    ParameterUpdateMode,
    ResetReason,
)
from synesthesia_machine.nodes.output import SEND_MIDI_TYPE_ID, create_midi_output_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import EngineFacade, InProcessEngineClient, PortKey
from synesthesia_machine.runtime.engine_client import ProcessEngineClient
from synesthesia_machine.runtime.engine_server import EngineServer

CLOCK_ID = UUID("00000000-0000-0000-0000-000000004501")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000004502")
MIDI_ID = UUID("00000000-0000-0000-0000-000000004503")
MIDI_SECOND_ID = UUID("00000000-0000-0000-0000-000000004504")
TARGET = "Exact Loopback Target"
OTHER = "Never Substitute This Port"


def _frame(notes: Mapping[tuple[int, int], int], tick_index: int = 1) -> MidiStateFrame:
    return MidiStateFrame(
        {MidiNoteKey(channel, note): velocity for (channel, note), velocity in notes.items()},
        frame_context(clock_id=CLOCK_ID, tick_index=tick_index),
        SOURCE_ID,
    )


def _configuration(
    port: str = TARGET,
    *,
    policy: VelocityUpdatePolicy = VelocityUpdatePolicy.IGNORE_WHILE_HELD,
    threshold: int = 4,
) -> MidiOutputConfiguration:
    return MidiOutputConfiguration(port, policy, threshold)


def _wait(service: MidiOutputService) -> None:
    assert service.wait_until_idle(2.0)


def _parameters(
    definition: NodeDefinition, overrides: Mapping[str, object] | None = None
) -> dict[str, ParameterValue]:
    parameters, errors = definition.parameter_values(overrides or {})
    assert errors == []
    return parameters


def _source_and_midi_document(output_port: str = "") -> GraphDocument:
    document = GraphDocument()
    document.add_node("test.midi_source", node_id=SOURCE_ID)
    document.add_node(
        SEND_MIDI_TYPE_ID,
        node_id=MIDI_ID,
        parameters={"output_port": output_port},
    )
    document.add_connection(SOURCE_ID, "value", MIDI_ID, "midi")
    return document


def _source_definition() -> NodeDefinition:
    return make_definition(
        "test.midi_source",
        output_type=PortType.MIDI_STATE,
        execution_kind=ExecutionKind.SOURCE,
    )


def test_send_midi_definition_is_demand_root_with_safe_defaults() -> None:
    definition = create_midi_output_definitions()[0]
    parameters = _parameters(definition)

    assert definition.type_id == SEND_MIDI_TYPE_ID
    assert definition.execution_kind is ExecutionKind.SINK
    assert definition.cache_policy is CachePolicy.NEVER
    assert definition.handles_no_data
    assert parameters == {
        "output_port": "",
        "velocity_update_policy": "Ignore while held",
        "velocity_change_threshold": 4,
    }
    output_port = definition.parameter("output_port")
    velocity_policy = definition.parameter("velocity_update_policy")
    velocity_threshold = definition.parameter("velocity_change_threshold")
    assert output_port is not None
    assert velocity_policy is not None
    assert velocity_threshold is not None
    assert output_port.update_mode is ParameterUpdateMode.RECOMPILE
    assert velocity_policy.choices == (
        "Ignore while held",
        "Retrigger",
        "Repeat note-on",
    )
    assert velocity_threshold.minimum == 0
    assert velocity_threshold.maximum == 127

    registry = NodeRegistry((_source_definition(), definition))
    plan = GraphCompiler(registry).compile(_source_and_midi_document().snapshot()).plan
    assert plan is not None
    assert plan.demand_roots == frozenset({MIDI_ID})
    source = plan.node(SOURCE_ID)
    assert source is not None and source.is_demanded


def test_empty_and_missing_selection_never_open_or_substitute() -> None:
    backend = MockMidiOutputBackend((OTHER,))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100}), _configuration(""))
        _wait(service)
        status = service.status()
        assert backend.enumeration_thread_ids
        assert backend.opened_names == []
        assert status.connection_state is MidiOutputConnectionState.UNSELECTED
        assert status.available_ports == (OTHER,)

        service.publish(_frame({(0, 60): 100}, 2), _configuration(TARGET))
        _wait(service)
        status = service.status()
        assert backend.opened_names == []
        assert status.connection_state is MidiOutputConnectionState.UNAVAILABLE
        assert status.selected_port == TARGET
        assert status.last_error is not None and TARGET in status.last_error
    finally:
        service.close()


def test_persistent_exact_port_diffs_off_before_on_on_sender_thread() -> None:
    caller_thread = threading.get_ident()
    backend = MockMidiOutputBackend((TARGET, OTHER))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(1, 64): 90, (0, 60): 100}), _configuration())
        _wait(service)
        port = backend.opened_ports[0]
        assert backend.opened_names == [TARGET]
        assert port.sent == [
            MidiMessage.all_notes_off(0),
            MidiMessage.all_notes_off(1),
            MidiMessage.note_on(MidiNoteKey(0, 60), 100),
            MidiMessage.note_on(MidiNoteKey(1, 64), 90),
        ]

        service.publish(_frame({(1, 64): 90, (0, 60): 100}, 2), _configuration())
        _wait(service)
        assert len(port.sent) == 4
        service.publish(_frame({(1, 64): 90, (0, 62): 110}, 3), _configuration())
        _wait(service)
        assert port.sent[-2:] == [
            MidiMessage.note_off(MidiNoteKey(0, 60)),
            MidiMessage.note_on(MidiNoteKey(0, 62), 110),
        ]
        assert backend.opened_names == [TARGET]

        status = service.status()
        sender_thread = status.sender_thread_id
        assert sender_thread is not None and sender_thread != caller_thread
        assert set(backend.enumeration_thread_ids) == {sender_thread}
        assert set(backend.open_thread_ids) == {sender_thread}
        assert set(port.send_thread_ids) == {sender_thread}
        assert status.active_note_count == 2
        assert status.active_channels == (0, 1)
    finally:
        service.close()
    assert set(backend.opened_ports[0].close_thread_ids) == {sender_thread}


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (VelocityUpdatePolicy.IGNORE_WHILE_HELD, []),
        (
            VelocityUpdatePolicy.RETRIGGER,
            [
                MidiMessage.note_off(MidiNoteKey(0, 60)),
                MidiMessage.note_on(MidiNoteKey(0, 60), 104),
            ],
        ),
        (
            VelocityUpdatePolicy.REPEAT_NOTE_ON,
            [MidiMessage.note_on(MidiNoteKey(0, 60), 104)],
        ),
    ],
)
def test_velocity_policies_and_threshold_boundary(
    policy: VelocityUpdatePolicy, expected: list[MidiMessage]
) -> None:
    backend = MockMidiOutputBackend((TARGET,))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        configuration = _configuration(policy=policy, threshold=4)
        service.publish(_frame({(0, 60): 100}), configuration)
        _wait(service)
        port = backend.opened_ports[0]
        initial_count = len(port.sent)

        service.publish(_frame({(0, 60): 103}, 2), configuration)
        _wait(service)
        assert len(port.sent) == initial_count
        service.publish(_frame({(0, 60): 104}, 3), configuration)
        _wait(service)
        assert port.sent[initial_count:] == expected

        unchanged_count = len(port.sent)
        zero_threshold = _configuration(policy=policy, threshold=0)
        service.publish(_frame({(0, 60): 104}, 4), zero_threshold)
        _wait(service)
        assert len(port.sent) == unchanged_count
    finally:
        service.close()


class _BlockingEnumerationBackend(MockMidiOutputBackend):
    def __init__(self) -> None:
        super().__init__((TARGET,))
        self.entered = threading.Event()
        self.release = threading.Event()

    def output_names(self) -> Sequence[str]:
        self.enumeration_thread_ids.append(threading.get_ident())
        self.entered.set()
        if not self.release.wait(2.0):
            raise TimeoutError("test did not release MIDI enumeration")
        return self.ports


def test_latest_state_mailbox_overwrites_stale_updates_without_blocking_publish() -> None:
    backend = _BlockingEnumerationBackend()
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        assert backend.entered.wait(1.0)
        service.publish(_frame({(0, 60): 70}), _configuration())
        service.publish(_frame({(0, 61): 80}, 2), _configuration())
        service.publish(_frame({(0, 62): 90}, 3), _configuration())
        assert service.status().dropped_state_updates == 2
        assert backend.opened_names == []

        backend.release.set()
        _wait(service)
        port = backend.opened_ports[0]
        note_ons = [
            message for message in port.sent if message.message_type is MidiMessageType.NOTE_ON
        ]
        assert note_ons == [MidiMessage.note_on(MidiNoteKey(0, 62), 90)]
    finally:
        backend.release.set()
        service.close()


class _BlockingNoteOnPort(MockMidiOutputPort):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._blocked_once = False

    def send(self, message: MidiMessage) -> None:
        self.send_thread_ids.append(threading.get_ident())
        if message.message_type is MidiMessageType.NOTE_ON and not self._blocked_once:
            self._blocked_once = True
            self.entered.set()
            if not self.release.wait(2.0):
                raise TimeoutError("test did not release blocked MIDI send")
        self.sent.append(message)


class _BlockingSendBackend(MockMidiOutputBackend):
    blocking_port: _BlockingNoteOnPort | None

    def __init__(self) -> None:
        super().__init__((TARGET,))
        self.blocking_port = None
        self.opened = threading.Event()

    def open_output(self, name: str) -> MockMidiOutputPort:
        self.open_thread_ids.append(threading.get_ident())
        if name not in self.ports:
            raise RuntimeError(f"Unknown mock MIDI output: {name}")
        port = _BlockingNoteOnPort(name)
        self.blocking_port = port
        self.opened_names.append(name)
        self.opened_ports.append(port)
        self.opened.set()
        return port


def test_latest_state_mailbox_stays_nonblocking_while_sender_is_blocked() -> None:
    backend = _BlockingSendBackend()
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 70}), _configuration())
        assert backend.opened.wait(1.0)
        port = backend.blocking_port
        assert port is not None and port.entered.wait(1.0)

        service.publish(_frame({(0, 61): 80}, 2), _configuration())
        service.publish(_frame({(0, 62): 90}, 3), _configuration())
        service.publish(_frame({(0, 63): 100}, 4), _configuration())
        assert service.status().dropped_state_updates == 2

        port.release.set()
        _wait(service)
        assert port.sent == [
            MidiMessage.all_notes_off(0),
            MidiMessage.note_on(MidiNoteKey(0, 60), 70),
            MidiMessage.note_off(MidiNoteKey(0, 60)),
            MidiMessage.note_on(MidiNoteKey(0, 63), 100),
        ]
    finally:
        if backend.blocking_port is not None:
            backend.blocking_port.release.set()
        service.close()


class _FailingOpenBackend(MockMidiOutputBackend):
    def __init__(self) -> None:
        super().__init__((TARGET, OTHER))
        self.fail_next_open = True

    def open_output(self, name: str) -> MockMidiOutputPort:
        if self.fail_next_open:
            self.open_thread_ids.append(threading.get_ident())
            self.fail_next_open = False
            raise OSError("synthetic open failure")
        port = super().open_output(name)
        assert isinstance(port, MockMidiOutputPort)
        return port


def test_exact_port_open_failure_reports_error_then_retries_same_latest_state() -> None:
    backend = _FailingOpenBackend()
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100}), _configuration())
        _wait(service)
        status = service.status()
        assert status.connection_state is MidiOutputConnectionState.ERROR
        assert status.selected_port == TARGET
        assert status.active_note_count == 0
        assert status.last_error is not None and "synthetic open failure" in status.last_error
        assert backend.opened_names == []

        service.refresh_outputs()
        _wait(service)
        assert backend.opened_names == [TARGET]
        assert service.status().connection_state is MidiOutputConnectionState.CONNECTED
        assert backend.opened_ports[0].sent == [
            MidiMessage.all_notes_off(0),
            MidiMessage.note_on(MidiNoteKey(0, 60), 100),
        ]
    finally:
        service.close()


class _FailOnAttemptPort(MockMidiOutputPort):
    def __init__(self, name: str, fail_on_attempt: int) -> None:
        super().__init__(name)
        self._fail_on_attempt = fail_on_attempt
        self._attempt_count = 0

    def send(self, message: MidiMessage) -> None:
        self.send_thread_ids.append(threading.get_ident())
        self._attempt_count += 1
        if self._attempt_count == self._fail_on_attempt:
            raise OSError(f"synthetic send attempt {self._fail_on_attempt} failure")
        self.sent.append(message)


class _FailingSendBackend(MockMidiOutputBackend):
    def __init__(self, fail_on_attempt: int) -> None:
        super().__init__((TARGET,))
        self._fail_on_attempt = fail_on_attempt

    def open_output(self, name: str) -> MockMidiOutputPort:
        self.open_thread_ids.append(threading.get_ident())
        if name not in self.ports:
            raise RuntimeError(f"Unknown mock MIDI output: {name}")
        port = _FailOnAttemptPort(name, self._fail_on_attempt)
        self.opened_names.append(name)
        self.opened_ports.append(port)
        return port


def test_open_guard_send_failure_panics_closes_and_never_rearms() -> None:
    backend = _FailingSendBackend(fail_on_attempt=1)
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100}), _configuration())
        _wait(service)
        port = backend.opened_ports[0]
        status = service.status()
        assert status.connection_state is MidiOutputConnectionState.ERROR
        assert status.active_note_count == 0
        assert status.last_error is not None and "send attempt 1 failure" in status.last_error
        assert port.sent == [MidiMessage.all_notes_off(0)]
        assert port.close_count == 1

        service.refresh_outputs()
        _wait(service)
        assert backend.opened_names == [TARGET]
    finally:
        service.close()


def test_partial_send_tracks_only_success_then_panics_that_note_and_channel() -> None:
    backend = _FailingSendBackend(fail_on_attempt=3)
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100, (0, 62): 110}), _configuration())
        _wait(service)
        port = backend.opened_ports[0]
        status = service.status()
        assert port.sent == [
            MidiMessage.all_notes_off(0),
            MidiMessage.note_on(MidiNoteKey(0, 60), 100),
            MidiMessage.note_off(MidiNoteKey(0, 60)),
            MidiMessage.all_notes_off(0),
        ]
        assert status.connection_state is MidiOutputConnectionState.ERROR
        assert status.active_note_count == 0
        assert status.active_channels == ()
        assert port.close_count == 1
    finally:
        service.close()


def test_send_failure_tracks_success_only_panics_closes_and_does_not_rearm() -> None:
    backend = MockMidiOutputBackend((TARGET,))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100}), _configuration())
        _wait(service)
        port = backend.opened_ports[0]
        port.fail_next_send = OSError("synthetic send failure")

        service.publish(_frame({(0, 62): 110}, 2), _configuration())
        _wait(service)
        status = service.status()
        assert status.connection_state is MidiOutputConnectionState.ERROR
        assert status.active_note_count == 0
        assert status.active_channels == ()
        assert status.last_error is not None and "synthetic send failure" in status.last_error
        assert port.sent[-2:] == [
            MidiMessage.note_off(MidiNoteKey(0, 60)),
            MidiMessage.all_notes_off(0),
        ]
        assert port.close_count == 1

        service.refresh_outputs()
        _wait(service)
        assert backend.opened_names == [TARGET]
    finally:
        service.close()


class _MutableBackend(MockMidiOutputBackend):
    fail_enumeration: bool

    def __init__(self, ports: tuple[str, ...]) -> None:
        super().__init__(ports)
        self.fail_enumeration = False

    def output_names(self) -> Sequence[str]:
        self.enumeration_thread_ids.append(threading.get_ident())
        if self.fail_enumeration:
            raise OSError("synthetic enumeration failure")
        return self.ports


def test_disappearance_panics_and_reappearance_uses_only_same_exact_name() -> None:
    backend = _MutableBackend((TARGET, OTHER))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(2, 67): 100}), _configuration())
        _wait(service)
        first_port = backend.opened_ports[0]

        backend.ports = (OTHER,)
        service.refresh_outputs()
        _wait(service)
        assert service.status().connection_state is MidiOutputConnectionState.UNAVAILABLE
        assert backend.opened_names == [TARGET]
        assert first_port.sent[-2:] == [
            MidiMessage.note_off(MidiNoteKey(2, 67)),
            MidiMessage.all_notes_off(2),
        ]
        assert first_port.close_count == 1

        backend.ports = (OTHER, TARGET)
        service.refresh_outputs()
        _wait(service)
        assert backend.opened_names == [TARGET, TARGET]
        assert service.status().connection_state is MidiOutputConnectionState.CONNECTED
        assert backend.opened_ports[1].sent[-1] == MidiMessage.note_on(MidiNoteKey(2, 67), 100)
    finally:
        service.close()


def test_enumeration_error_panics_active_port_and_requires_new_desired_state() -> None:
    backend = _MutableBackend((TARGET,))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(3, 70): 100}), _configuration())
        _wait(service)
        port = backend.opened_ports[0]

        backend.fail_enumeration = True
        service.refresh_outputs()
        _wait(service)
        status = service.status()
        assert status.connection_state is MidiOutputConnectionState.ERROR
        assert status.active_note_count == 0
        assert (
            status.last_error is not None and "synthetic enumeration failure" in status.last_error
        )
        assert port.sent[-2:] == [
            MidiMessage.note_off(MidiNoteKey(3, 70)),
            MidiMessage.all_notes_off(3),
        ]
        assert port.close_count == 1

        backend.fail_enumeration = False
        service.refresh_outputs()
        _wait(service)
        assert backend.opened_names == [TARGET]
    finally:
        service.close()


def test_panic_port_switch_and_shutdown_use_explicit_offs_then_cc123() -> None:
    backend = MockMidiOutputBackend((TARGET, OTHER))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    _wait(service)
    service.publish(_frame({(0, 60): 100, (1, 64): 90}), _configuration())
    _wait(service)
    first = backend.opened_ports[0]

    service.publish(_frame({(2, 67): 80}, 2), _configuration(OTHER))
    _wait(service)
    assert first.sent[-4:] == [
        MidiMessage.note_off(MidiNoteKey(0, 60)),
        MidiMessage.note_off(MidiNoteKey(1, 64)),
        MidiMessage.all_notes_off(0),
        MidiMessage.all_notes_off(1),
    ]
    assert first.close_count == 1
    second = backend.opened_ports[1]

    service.panic()
    assert second.sent[-2:] == [
        MidiMessage.note_off(MidiNoteKey(2, 67)),
        MidiMessage.all_notes_off(2),
    ]
    service.refresh_outputs()
    _wait(service)
    assert backend.opened_names == [TARGET, OTHER]
    service.close()
    assert second.close_count == 1
    assert service.status().connection_state is MidiOutputConnectionState.CLOSED


def test_failed_panic_is_best_effort_marks_error_closes_and_clears_state() -> None:
    backend = MockMidiOutputBackend((TARGET,))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(4, 72): 100}), _configuration())
        _wait(service)
        port = backend.opened_ports[0]
        port.fail_next_send = OSError("synthetic panic failure")

        service.panic()
        status = service.status()
        assert status.connection_state is MidiOutputConnectionState.ERROR
        assert status.active_note_count == 0
        assert status.active_channels == ()
        assert status.last_error is not None and "synthetic panic failure" in status.last_error
        assert port.sent[-1] == MidiMessage.all_notes_off(4)
        assert port.close_count == 1

        service.refresh_outputs()
        _wait(service)
        assert backend.opened_names == [TARGET]
    finally:
        service.close()


def test_close_is_idempotent_and_rejects_new_work_after_shutdown() -> None:
    backend = MockMidiOutputBackend((TARGET,))
    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    _wait(service)
    service.publish(_frame({(0, 60): 100}), _configuration())
    _wait(service)
    port = backend.opened_ports[0]

    service.close()
    service.close()
    service.panic()
    service.request_panic()

    assert port.sent[-2:] == [
        MidiMessage.note_off(MidiNoteKey(0, 60)),
        MidiMessage.all_notes_off(0),
    ]
    assert port.close_count == 1
    assert service.status().connection_state is MidiOutputConnectionState.CLOSED
    with pytest.raises(RuntimeError, match="service is closed"):
        service.publish(_frame({}), _configuration())
    with pytest.raises(RuntimeError, match="service is closed"):
        service.refresh_outputs()


class _FakeMidoPort:
    def __init__(self) -> None:
        self.messages: list[mido.Message] = []
        self.close_count = 0

    def send(self, message: mido.Message) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.close_count += 1


class _FakeMidoBackend:
    def __init__(self) -> None:
        self.names = (TARGET,)
        self.enumeration_count = 0
        self.opened: list[str] = []
        self.port = _FakeMidoPort()

    def get_output_names(self) -> Sequence[str]:
        self.enumeration_count += 1
        return self.names

    def open_output(self, name: str) -> _FakeMidoPort:
        self.opened.append(name)
        return self.port


def test_mido_adapter_enumerates_without_opening_and_preserves_wire_shape() -> None:
    fake = _FakeMidoBackend()
    backend = MidoRtMidiBackend(fake)
    assert backend.output_names() == (TARGET,)
    assert fake.opened == []
    with pytest.raises(RuntimeError, match="not currently available"):
        backend.open_output(OTHER)
    assert fake.opened == []

    port = backend.open_output(TARGET)
    port.send(MidiMessage.note_on(MidiNoteKey(2, 69), 101))
    port.send(MidiMessage.all_notes_off(2))
    port.close()
    assert fake.opened == [TARGET]
    assert str(fake.port.messages[0]) == "note_on channel=2 note=69 velocity=101 time=0"
    assert str(fake.port.messages[1]) == "control_change channel=2 control=123 value=0 time=0"
    assert fake.port.close_count == 1


def test_windows_rtmidi_indices_are_hidden_and_friendly_loopmidi_name_opens_raw_port() -> None:
    fake = _FakeMidoBackend()
    fake.names = ("loopMIDI Port 1 2", "loopMIDI Port 3")
    backend = MidoRtMidiBackend(fake, normalize_windows_names=True)

    assert backend.output_names() == ("loopMIDI Port 1", "loopMIDI Port")
    assert tuple(
        (device.device_id, device.display_name) for device in backend.output_devices()
    ) == (
        ("loopMIDI Port 1 2", "loopMIDI Port 1"),
        ("loopMIDI Port 3", "loopMIDI Port"),
    )
    assert fake.opened == []

    service = MidiOutputService(backend, refresh_interval_s=3600.0)
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100}), _configuration("loopMIDI Port"))
        _wait(service)

        status = service.status()
        assert status.connection_state is MidiOutputConnectionState.CONNECTED
        assert status.selected_port == "loopMIDI Port"
        assert status.available_ports == ("loopMIDI Port 1", "loopMIDI Port")
        assert fake.opened == ["loopMIDI Port 3"]
        assert fake.port.messages[-1] == mido.Message("note_on", channel=0, note=60, velocity=100)

        enumeration_count = fake.enumeration_count
        service.publish(_frame({(0, 61): 90}), _configuration("loopMIDI Port"))
        _wait(service)
        assert fake.enumeration_count == enumeration_count
    finally:
        service.close()


def test_midi_service_opens_the_stable_raw_id_committed_by_device_picker() -> None:
    fake = _FakeMidoBackend()
    fake.names = ("loopMIDI Port 3",)
    service = MidiOutputService(
        MidoRtMidiBackend(fake, normalize_windows_names=True),
        refresh_interval_s=3600.0,
    )
    try:
        _wait(service)
        service.publish(_frame({(0, 60): 100}), _configuration("loopMIDI Port 3"))
        _wait(service)

        assert service.status().connection_state is MidiOutputConnectionState.CONNECTED
        assert service.status().selected_port == "loopMIDI Port 3"
        assert fake.opened == ["loopMIDI Port 3"]
    finally:
        service.close()


def test_windows_midi_alias_collisions_keep_unambiguous_raw_names() -> None:
    fake = _FakeMidoBackend()
    fake.names = ("Device 1", "Device 2")
    backend = MidoRtMidiBackend(fake, normalize_windows_names=True)

    assert backend.output_names() == fake.names


def _published_states() -> list[tuple[MidiStateFrame, MidiOutputConfiguration]]:
    return []


@dataclass(slots=True)
class _RecordingService:
    published: list[tuple[MidiStateFrame, MidiOutputConfiguration]] = field(
        default_factory=_published_states
    )
    panic_count: int = 0
    request_panic_count: int = 0
    close_count: int = 0
    service_status: MidiServiceStatus = field(
        default_factory=lambda: MidiServiceStatus(
            MidiOutputConnectionState.UNSELECTED,
            "",
            (TARGET,),
            0,
            (),
            0,
            None,
            None,
        )
    )

    def publish(self, frame: MidiStateFrame, configuration: MidiOutputConfiguration) -> None:
        self.published.append((frame, configuration))

    def request_panic(self) -> None:
        self.request_panic_count += 1

    def panic(self, timeout_s: float = 2.0) -> None:
        del timeout_s
        self.panic_count += 1

    def refresh_outputs(self) -> None:
        return

    def status(self) -> MidiServiceStatus:
        return self.service_status

    def wait_until_idle(self, timeout_s: float = 2.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        self.close_count += 1


def test_runtime_no_data_status_reset_and_close_lifecycle() -> None:
    service = _RecordingService()
    definition = create_midi_output_definitions(service_factory=lambda: service)[0]
    runtime = definition.runtime_factory(MIDI_ID)
    parameters = _parameters(definition, {"output_port": TARGET})
    midi = _frame({(0, 60): 100})

    runtime.process({"midi": midi}, parameters, midi.context)
    assert service.published == [(midi, _configuration())]
    runtime.process({"midi": NoData}, parameters, midi.context)
    runtime.process({"midi": NoData}, parameters, midi.context)
    assert service.request_panic_count == 1
    runtime.reset(ResetReason.SOURCE_RESTARTED)
    runtime.process({"midi": NoData}, parameters, midi.context)
    assert service.panic_count == 1
    assert service.request_panic_count == 2
    runtime.close()
    assert service.close_count == 1


def test_unavailable_runtime_status_becomes_recoverable_scheduler_error() -> None:
    service = _RecordingService(
        service_status=MidiServiceStatus(
            MidiOutputConnectionState.UNAVAILABLE,
            TARGET,
            (OTHER,),
            0,
            (),
            0,
            f"MIDI output {TARGET!r} is unavailable",
            None,
        )
    )
    midi_definition = create_midi_output_definitions(service_factory=lambda: service)[0]
    facade = EngineFacade(NodeRegistry((_source_definition(), midi_definition)))
    try:
        assert facade.activate(_source_and_midi_document(TARGET).snapshot()).plan is not None
        result = facade.tick(
            frame_context(clock_id=SOURCE_ID),
            source_values={PortKey(SOURCE_ID, "value"): _frame({(0, 60): 100})},
        )
        assert len(result.errors) == 1
        error = result.errors[0]
        assert error.node_id == MIDI_ID
        assert error.code == "midi_output_unavailable"
        assert error.recoverable
        assert TARGET in error.message
    finally:
        facade.close()


def test_source_reset_panics_midi_sink_in_that_clock_component() -> None:
    services: list[_RecordingService] = []

    def service_factory() -> _RecordingService:
        service = _RecordingService()
        services.append(service)
        return service

    midi_definition = create_midi_output_definitions(service_factory=service_factory)[0]
    facade = EngineFacade(NodeRegistry((_source_definition(), midi_definition)))
    try:
        assert facade.activate(_source_and_midi_document().snapshot()).plan is not None
        facade.reset_source(SOURCE_ID, ResetReason.SOURCE_RESTARTED)
        assert services[0].panic_count == 1
    finally:
        facade.close()
    assert services[0].close_count == 1


def test_facade_stop_closes_scheduler_even_when_midi_panic_times_out() -> None:
    # A MIDI device that cannot confirm all-notes-off within the panic
    # timeout makes service.panic() raise; EngineFacade.stop() must still
    # close the scheduler, or its open MIDI ports and sender threads leak
    # on every auto-stop in the long-lived child engine.
    class _PanicTimeoutService(_RecordingService):
        def panic(self, timeout_s: float = 2.0) -> None:
            del timeout_s
            self.panic_count += 1
            raise TimeoutError("panic confirmation timed out")

    service = _PanicTimeoutService()
    midi_definition = create_midi_output_definitions(service_factory=lambda: service)[0]
    facade = EngineFacade(NodeRegistry((_source_definition(), midi_definition)))
    try:
        assert facade.activate(_source_and_midi_document().snapshot()).plan is not None
        with pytest.raises(TimeoutError):
            facade.stop()
    finally:
        facade.close()
    # Two panics: the direct one (which timed out) and the close-time
    # reset, which Scheduler.close performs on every runtime.
    assert service.panic_count == 2
    # close() ran despite the panic timeout: the leaked-scheduler defect is
    # that this would be 0 on the pre-fix ordering (detach before panic).
    assert service.close_count == 1


def test_output_port_recompile_retires_old_runtime_before_using_new_service() -> None:
    services: list[_RecordingService] = []

    def service_factory() -> _RecordingService:
        service = _RecordingService()
        services.append(service)
        return service

    midi_definition = create_midi_output_definitions(service_factory=service_factory)[0]
    facade = EngineFacade(NodeRegistry((_source_definition(), midi_definition)))
    document = _source_and_midi_document()
    try:
        assert facade.activate(document.snapshot()).plan is not None
        previous = services[0]

        document.set_parameter(MIDI_ID, "output_port", TARGET)
        assert facade.activate(document.snapshot()).plan is not None

        assert len(services) == 2
        assert previous.panic_count == 1
        assert previous.close_count == 1
        assert services[1].panic_count == 0
        assert services[1].close_count == 0
    finally:
        facade.close()
    assert services[1].close_count == 1


def test_multiple_midi_statuses_are_sorted_and_exactly_filterable() -> None:
    services: list[_RecordingService] = []

    def service_factory() -> _RecordingService:
        service = _RecordingService()
        services.append(service)
        return service

    midi_definition = create_midi_output_definitions(service_factory=service_factory)[0]
    registry = NodeRegistry((_source_definition(), midi_definition))
    document = _source_and_midi_document()
    document.add_node(SEND_MIDI_TYPE_ID, node_id=MIDI_SECOND_ID)
    document.add_connection(SOURCE_ID, "value", MIDI_SECOND_ID, "midi")
    engine = InProcessEngineClient(registry)
    try:
        assert engine.activate(document.snapshot()).activated
        statuses = engine.midi_output_status()
        assert tuple(status.node_id for status in statuses) == (MIDI_ID, MIDI_SECOND_ID)
        assert engine.midi_output_status(MIDI_SECOND_ID) == (statuses[1],)
    finally:
        engine.close()
    assert len(services) == 2
    assert all(service.close_count == 1 for service in services)


def test_scheduler_facade_engine_status_and_retirement_propagate_safety() -> None:
    services: list[_RecordingService] = []

    def service_factory() -> _RecordingService:
        service = _RecordingService()
        services.append(service)
        return service

    midi_definition = create_midi_output_definitions(service_factory=service_factory)[0]
    registry = NodeRegistry((_source_definition(), midi_definition))
    document = _source_and_midi_document()
    engine = InProcessEngineClient(registry)
    try:
        assert engine.activate(document.snapshot()).activated
        statuses = engine.midi_output_status()
        assert len(statuses) == 1
        assert statuses[0].node_id == MIDI_ID
        assert statuses[0].available_ports == (TARGET,)
        assert engine.midi_output_status(UUID(int=0)) == ()

        engine.stop()
        engine.reload()
        engine.panic()
        assert services[0].panic_count == 3
    finally:
        engine.close()
    assert services[0].close_count == 1

    facade = EngineFacade(registry)
    document = _source_and_midi_document()
    assert facade.activate(document.snapshot()).plan is not None
    active = services[1]
    facade.tick(
        frame_context(clock_id=SOURCE_ID),
        source_values={PortKey(SOURCE_ID, "value"): _frame({(0, 60): 100})},
    )
    document.remove_node(MIDI_ID)
    assert facade.activate(document.snapshot()).plan is not None
    assert active.panic_count == 1
    assert active.close_count == 1
    facade.close()


def test_server_midi_status_query_returns_compact_typed_response() -> None:
    expected = MidiOutputStatus(
        MIDI_ID,
        MidiOutputConnectionState.CONNECTED,
        TARGET,
        (TARGET,),
        2,
        (0, 1),
        3,
    )

    class _EngineProbe:
        def midi_output_status(
            self, output_node_id: UUID | None = None
        ) -> tuple[MidiOutputStatus, ...]:
            assert output_node_id == MIDI_ID
            return (expected,)

    server = object.__new__(EngineServer)
    server._engine = _EngineProbe()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    server._graph_revision = 7  # pyright: ignore[reportPrivateUsage]
    response = server._handle(  # pyright: ignore[reportPrivateUsage]
        QueryMidiOutputStatus("midi-status", 7, MIDI_ID)
    )
    assert response == MidiOutputStatusResponse("midi-status", 7, (expected,))


def test_spawned_child_midi_status_query_with_no_output_node_touches_no_hardware() -> None:
    client = ProcessEngineClient(request_timeout_s=1.5, close_timeout_s=0.5)
    try:
        document = GraphDocument()
        document.add_node(
            "synmachine.utility.number",
            parameters={"number_type": "FLOAT", "float_value": 0.5},
        )
        assert client.activate(document.snapshot()).activated
        assert client.midi_output_status() == ()
    finally:
        client.close()
