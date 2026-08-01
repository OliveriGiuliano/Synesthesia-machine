"""Persistent, exact-port MIDI output with latest-state backpressure and panic safety."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast

import mido

from synesthesia_machine.contracts import MidiNoteKey, MidiOutputConnectionState, MidiStateFrame


class VelocityUpdatePolicy(StrEnum):
    IGNORE_WHILE_HELD = "Ignore while held"
    RETRIGGER = "Retrigger"
    REPEAT_NOTE_ON = "Repeat note-on"


class MidiMessageType(StrEnum):
    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    CONTROL_CHANGE = "control_change"


@dataclass(frozen=True, slots=True)
class MidiMessage:
    message_type: MidiMessageType
    channel: int
    note: int | None = None
    velocity: int | None = None
    control: int | None = None
    value: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            raise ValueError("MIDI message channel must be in the range 0..15")
        if self.message_type in {MidiMessageType.NOTE_ON, MidiMessageType.NOTE_OFF}:
            if self.note is None or not 0 <= self.note <= 127:
                raise ValueError("MIDI note message requires a note in the range 0..127")
            if self.velocity is None or not 0 <= self.velocity <= 127:
                raise ValueError("MIDI note message requires a velocity in the range 0..127")
            if self.control is not None or self.value is not None:
                raise ValueError("MIDI note messages cannot contain control-change values")
        elif self.message_type is MidiMessageType.CONTROL_CHANGE:
            if self.control is None or not 0 <= self.control <= 127:
                raise ValueError("MIDI control change requires a control in the range 0..127")
            if self.value is None or not 0 <= self.value <= 127:
                raise ValueError("MIDI control change requires a value in the range 0..127")
            if self.note is not None or self.velocity is not None:
                raise ValueError("MIDI control changes cannot contain note values")

    @classmethod
    def note_on(cls, key: MidiNoteKey, velocity: int) -> MidiMessage:
        return cls(MidiMessageType.NOTE_ON, key.channel, key.note, velocity)

    @classmethod
    def note_off(cls, key: MidiNoteKey) -> MidiMessage:
        return cls(MidiMessageType.NOTE_OFF, key.channel, key.note, 0)

    @classmethod
    def all_notes_off(cls, channel: int) -> MidiMessage:
        return cls(MidiMessageType.CONTROL_CHANGE, channel, control=123, value=0)


class MidiOutputPort(Protocol):
    def send(self, message: MidiMessage) -> None: ...

    def close(self) -> None: ...


class MidiOutputBackend(Protocol):
    def output_names(self) -> Sequence[str]: ...

    def open_output(self, name: str) -> MidiOutputPort: ...


class _MidoOutputPort(Protocol):
    def send(self, message: mido.Message) -> None: ...

    def close(self) -> None: ...


class _MidoBackendFacade(Protocol):
    def get_output_names(self) -> Sequence[str]: ...

    def open_output(self, name: str) -> _MidoOutputPort: ...


class _MidoPortAdapter:
    def __init__(self, port: _MidoOutputPort) -> None:
        self._port = port

    def send(self, message: MidiMessage) -> None:
        if message.message_type is MidiMessageType.CONTROL_CHANGE:
            assert message.control is not None
            assert message.value is not None
            wire_message = mido.Message(
                message.message_type.value,
                channel=message.channel,
                control=message.control,
                value=message.value,
            )
        else:
            assert message.note is not None
            assert message.velocity is not None
            wire_message = mido.Message(
                message.message_type.value,
                channel=message.channel,
                note=message.note,
                velocity=message.velocity,
            )
        self._port.send(wire_message)

    def close(self) -> None:
        self._port.close()


class MidoRtMidiBackend:
    """Real Mido/python-rtmidi adapter; enumeration never opens an output."""

    def __init__(self, backend: _MidoBackendFacade | None = None) -> None:
        self._backend = backend or cast(
            _MidoBackendFacade,
            mido.Backend("mido.backends.rtmidi"),  # pyright: ignore[reportUnknownMemberType]
        )

    def output_names(self) -> Sequence[str]:
        return tuple(self._backend.get_output_names())

    def open_output(self, name: str) -> MidiOutputPort:
        if name not in self.output_names():
            raise MidiPortUnavailableError(f"MIDI output {name!r} is not currently available")
        return _MidoPortAdapter(self._backend.open_output(name))


def _message_list() -> list[MidiMessage]:
    return []


def _integer_list() -> list[int]:
    return []


@dataclass(slots=True)
class MockMidiOutputPort:
    """Deterministic persistent port used by automated tests and diagnostics."""

    name: str
    sent: list[MidiMessage] = field(default_factory=_message_list)
    send_thread_ids: list[int] = field(default_factory=_integer_list)
    close_thread_ids: list[int] = field(default_factory=_integer_list)
    close_count: int = 0
    fail_next_send: BaseException | None = None

    def send(self, message: MidiMessage) -> None:
        self.send_thread_ids.append(threading.get_ident())
        failure = self.fail_next_send
        self.fail_next_send = None
        if failure is not None:
            raise failure
        self.sent.append(message)

    def close(self) -> None:
        self.close_thread_ids.append(threading.get_ident())
        self.close_count += 1


def _name_list() -> list[str]:
    return []


def _mock_port_list() -> list[MockMidiOutputPort]:
    return []


@dataclass(slots=True)
class MockMidiOutputBackend:
    """Mockable backend with exact-name open semantics and no implicit selection."""

    ports: tuple[str, ...] = ("Mock MIDI Out",)
    opened_names: list[str] = field(default_factory=_name_list)
    opened_ports: list[MockMidiOutputPort] = field(default_factory=_mock_port_list)
    enumeration_thread_ids: list[int] = field(default_factory=_integer_list)
    open_thread_ids: list[int] = field(default_factory=_integer_list)

    def output_names(self) -> Sequence[str]:
        self.enumeration_thread_ids.append(threading.get_ident())
        return self.ports

    def open_output(self, name: str) -> MidiOutputPort:
        self.open_thread_ids.append(threading.get_ident())
        if name not in self.ports:
            raise MidiPortUnavailableError(f"Unknown mock MIDI output: {name}")
        port = MockMidiOutputPort(name)
        self.opened_names.append(name)
        self.opened_ports.append(port)
        return port


class MidiPortUnavailableError(RuntimeError):
    """The exact selected output is not currently enumerated."""


@dataclass(frozen=True, slots=True)
class MidiOutputConfiguration:
    output_port: str = ""
    velocity_policy: VelocityUpdatePolicy = VelocityUpdatePolicy.IGNORE_WHILE_HELD
    velocity_change_threshold: int = 4

    def __post_init__(self) -> None:
        if not 0 <= self.velocity_change_threshold <= 127:
            raise ValueError("velocity_change_threshold must be in the range 0..127")


@dataclass(frozen=True, slots=True)
class MidiServiceStatus:
    connection_state: MidiOutputConnectionState
    selected_port: str
    available_ports: tuple[str, ...]
    active_note_count: int
    active_channels: tuple[int, ...]
    dropped_state_updates: int
    last_error: str | None
    sender_thread_id: int | None


@dataclass(frozen=True, slots=True)
class _DesiredState:
    notes: tuple[tuple[MidiNoteKey, int], ...]
    configuration: MidiOutputConfiguration

    @classmethod
    def from_frame(
        cls, frame: MidiStateFrame, configuration: MidiOutputConfiguration
    ) -> _DesiredState:
        return cls(tuple(sorted(frame.notes.items())), configuration)


class MidiOutputServiceProtocol(Protocol):
    def publish(self, frame: MidiStateFrame, configuration: MidiOutputConfiguration) -> None: ...

    def request_panic(self) -> None: ...

    def panic(self, timeout_s: float = 2.0) -> None: ...

    def refresh_outputs(self) -> None: ...

    def status(self) -> MidiServiceStatus: ...

    def wait_until_idle(self, timeout_s: float = 2.0) -> bool: ...

    def close(self) -> None: ...


type MidiOutputServiceFactory = Callable[[], MidiOutputServiceProtocol]


class MidiOutputService:
    """Own one logical output session and reconcile only its latest desired state.

    Graph calls only publish immutable desired state into a one-slot mailbox. Enumeration,
    port opening, all sends, panic, and port closure happen on the dedicated sender thread.
    """

    def __init__(
        self,
        backend: MidiOutputBackend | None = None,
        *,
        refresh_interval_s: float = 1.0,
    ) -> None:
        if refresh_interval_s <= 0.0:
            raise ValueError("refresh_interval_s must be positive")
        self._backend = backend or MidoRtMidiBackend()
        self._refresh_interval_s = refresh_interval_s
        self._condition = threading.Condition()
        self._status_lock = threading.Lock()
        self._pending: _DesiredState | None = None
        self._latest: _DesiredState | None = None
        self._busy = False
        self._closing = False
        self._closed = False
        self._refresh_requested = True
        self._panic_requested = 0
        self._panic_completed = 0
        self._dropped_state_updates = 0

        self._port: MidiOutputPort | None = None
        self._port_name: str | None = None
        self._selected_port = ""
        self._available_ports: tuple[str, ...] = ()
        self._sent: dict[MidiNoteKey, int] = {}
        self._used_channels: set[int] = set()
        self._connection_state = MidiOutputConnectionState.UNSELECTED
        self._last_error: str | None = None
        self._sender_thread_id: int | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="midi-output-sender",
            daemon=True,
        )
        self._thread.start()

    def publish(self, frame: MidiStateFrame, configuration: MidiOutputConfiguration) -> None:
        desired = _DesiredState.from_frame(frame, configuration)
        with self._condition:
            self._ensure_open()
            if self._pending is not None:
                self._dropped_state_updates += 1
            self._pending = desired
            self._latest = desired
            self._condition.notify_all()

    def request_panic(self) -> None:
        with self._condition:
            if self._closing or self._closed:
                return
            self._pending = None
            self._latest = None
            self._panic_requested += 1
            self._condition.notify_all()

    def panic(self, timeout_s: float = 2.0) -> None:
        with self._condition:
            if self._closing or self._closed:
                return
            self._pending = None
            self._latest = None
            self._panic_requested += 1
            generation = self._panic_requested
            self._condition.notify_all()
            deadline = time.monotonic() + max(0.0, timeout_s)
            while self._panic_completed < generation and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise TimeoutError("Timed out waiting for MIDI panic")
                self._condition.wait(remaining)

    def refresh_outputs(self) -> None:
        with self._condition:
            self._ensure_open()
            self._refresh_requested = True
            self._condition.notify_all()

    def status(self) -> MidiServiceStatus:
        with self._condition:
            dropped = self._dropped_state_updates
        with self._status_lock:
            active_channels = tuple(sorted({key.channel for key in self._sent}))
            return MidiServiceStatus(
                self._connection_state,
                self._selected_port,
                self._available_ports,
                len(self._sent),
                active_channels,
                dropped,
                self._last_error,
                self._sender_thread_id,
            )

    def wait_until_idle(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._condition:
            while (
                self._pending is not None
                or self._busy
                or self._refresh_requested
                or self._panic_completed < self._panic_requested
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        with self._condition:
            if self._closing or self._closed:
                return
            self._closing = True
            self._pending = None
            self._latest = None
            self._condition.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        with self._condition:
            if not self._closed:
                raise TimeoutError("MIDI sender thread did not close")

    def _run(self) -> None:
        with self._status_lock:
            self._sender_thread_id = threading.get_ident()
        next_refresh = 0.0
        try:
            while True:
                action: str
                desired: _DesiredState | None = None
                panic_generation = 0
                with self._condition:
                    while True:
                        now = time.monotonic()
                        refresh_due = now >= next_refresh
                        if self._closing:
                            action = "close"
                            break
                        if self._panic_completed < self._panic_requested:
                            action = "panic"
                            panic_generation = self._panic_requested
                            break
                        if self._refresh_requested or refresh_due:
                            action = "refresh"
                            self._refresh_requested = False
                            break
                        if self._pending is not None:
                            action = "state"
                            desired = self._pending
                            self._pending = None
                            break
                        self._condition.wait(max(0.0, next_refresh - now))
                    self._busy = True

                if action == "close":
                    self._panic_current(close_port=True)
                    with self._status_lock:
                        self._connection_state = MidiOutputConnectionState.CLOSED
                    return
                if action == "panic":
                    panic_error = self._panic_current(close_port=False)
                    if panic_error is not None:
                        self._mark_output_error(
                            "MIDI panic was best-effort",
                            panic_error,
                        )
                    with self._condition:
                        self._panic_completed = max(self._panic_completed, panic_generation)
                elif action == "state":
                    assert desired is not None
                    self._apply_desired(desired)
                else:
                    self._refresh_port_names()
                    next_refresh = time.monotonic() + self._refresh_interval_s
                    with self._condition:
                        latest = self._latest
                    if latest is not None:
                        self._apply_desired(latest)

                with self._condition:
                    self._busy = False
                    self._condition.notify_all()
        except Exception as error:
            self._panic_current(close_port=True, initial_error=error)
            with self._status_lock:
                self._connection_state = MidiOutputConnectionState.ERROR
                self._last_error = f"{type(error).__name__}: {error}"
        finally:
            self._close_port()
            with self._condition:
                self._busy = False
                self._closed = True
                self._condition.notify_all()

    def _apply_desired(self, desired: _DesiredState) -> None:
        configuration = desired.configuration
        selected_port = configuration.output_port
        with self._status_lock:
            self._selected_port = selected_port
            available_ports = self._available_ports

        if self._port_name is not None and self._port_name != selected_port:
            self._panic_current(close_port=True)
        if not selected_port:
            if self._port is not None:
                self._panic_current(close_port=True)
            with self._status_lock:
                self._connection_state = MidiOutputConnectionState.UNSELECTED
                self._last_error = None
            return
        if self._port is None:
            if selected_port not in available_ports:
                with self._status_lock:
                    self._connection_state = MidiOutputConnectionState.UNAVAILABLE
                    self._last_error = f"MIDI output {selected_port!r} is not currently available"
                return
            if not self._open_exact_port(selected_port, desired):
                return
        self._reconcile(dict(desired.notes), configuration)

    def _open_exact_port(self, selected_port: str, desired: _DesiredState) -> bool:
        with self._status_lock:
            self._connection_state = MidiOutputConnectionState.CONNECTING
        try:
            port = self._backend.open_output(selected_port)
        except Exception as error:
            with self._status_lock:
                self._connection_state = MidiOutputConnectionState.ERROR
                self._last_error = f"Could not open MIDI output {selected_port!r}: {error}"
            return False
        self._port = port
        self._port_name = selected_port
        for channel in sorted({key.channel for key, _ in desired.notes}):
            try:
                port.send(MidiMessage.all_notes_off(channel))
            except Exception as error:
                self._handle_send_failure(error, attempted_channel=channel)
                return False
        with self._status_lock:
            self._connection_state = MidiOutputConnectionState.CONNECTED
            self._last_error = None
        return True

    def _reconcile(
        self,
        desired: Mapping[MidiNoteKey, int],
        configuration: MidiOutputConfiguration,
    ) -> None:
        with self._status_lock:
            sent = dict(self._sent)

        note_offs: list[MidiNoteKey] = []
        note_ons: list[tuple[MidiNoteKey, int]] = []
        for key, old_velocity in sorted(sent.items()):
            new_velocity = desired.get(key)
            if new_velocity is None:
                note_offs.append(key)
                continue
            if new_velocity == old_velocity or (
                abs(new_velocity - old_velocity) < configuration.velocity_change_threshold
            ):
                continue
            if configuration.velocity_policy is VelocityUpdatePolicy.RETRIGGER:
                note_offs.append(key)
                note_ons.append((key, new_velocity))
            elif configuration.velocity_policy is VelocityUpdatePolicy.REPEAT_NOTE_ON:
                note_ons.append((key, new_velocity))
        for key, velocity in sorted(desired.items()):
            if key not in sent:
                note_ons.append((key, velocity))

        for key in note_offs:
            if not self._send(MidiMessage.note_off(key)):
                return
            with self._status_lock:
                self._sent.pop(key, None)
        for key, velocity in note_ons:
            if not self._send(MidiMessage.note_on(key, velocity)):
                return
            with self._status_lock:
                self._sent[key] = velocity
                self._used_channels.add(key.channel)

    def _send(self, message: MidiMessage) -> bool:
        port = self._port
        if port is None:
            return False
        try:
            port.send(message)
        except Exception as error:
            self._handle_send_failure(error, attempted_channel=message.channel)
            return False
        return True

    def _handle_send_failure(self, error: Exception, *, attempted_channel: int) -> None:
        self._discard_desired_state()
        self._panic_current(
            close_port=True,
            initial_error=error,
            additional_channels=(attempted_channel,),
        )
        self._mark_output_error("MIDI output send failed", error)

    def _mark_output_error(self, summary: str, error: BaseException) -> None:
        with self._status_lock:
            selected_port = self._selected_port
            available = selected_port in self._available_ports
            self._connection_state = (
                MidiOutputConnectionState.ERROR
                if available
                else MidiOutputConnectionState.UNAVAILABLE
            )
            self._last_error = f"{summary}: {type(error).__name__}: {error}"

    def _panic_current(
        self,
        *,
        close_port: bool,
        initial_error: BaseException | None = None,
        additional_channels: Sequence[int] = (),
    ) -> BaseException | None:
        port = self._port
        with self._status_lock:
            sent_keys = tuple(sorted(self._sent))
            channels = tuple(
                sorted(
                    self._used_channels
                    | {key.channel for key in sent_keys}
                    | set(additional_channels)
                )
            )
        first_error = initial_error
        if port is not None:
            for key in sent_keys:
                try:
                    port.send(MidiMessage.note_off(key))
                except Exception as error:
                    if first_error is None:
                        first_error = error
            for channel in channels:
                try:
                    port.send(MidiMessage.all_notes_off(channel))
                except Exception as error:
                    if first_error is None:
                        first_error = error
        with self._status_lock:
            self._sent.clear()
            self._used_channels.clear()
            if first_error is not None:
                self._last_error = (
                    f"MIDI panic was best-effort: {type(first_error).__name__}: {first_error}"
                )
        if close_port or first_error is not None:
            self._close_port()
        return first_error

    def _refresh_port_names(self) -> None:
        try:
            names = tuple(dict.fromkeys(self._backend.output_names()))
        except Exception as error:
            self._discard_desired_state()
            had_open_port = self._port is not None
            self._panic_current(close_port=True, initial_error=error)
            with self._status_lock:
                self._available_ports = ()
                if self._selected_port or had_open_port:
                    self._connection_state = MidiOutputConnectionState.ERROR
                self._last_error = (
                    f"Could not enumerate MIDI outputs: {type(error).__name__}: {error}"
                )
            return
        with self._status_lock:
            self._available_ports = names
            selected_port = self._selected_port
        if self._port_name is not None and self._port_name not in names:
            disappeared_port = self._port_name
            self._panic_current(close_port=True)
            with self._status_lock:
                self._connection_state = MidiOutputConnectionState.UNAVAILABLE
                self._last_error = f"MIDI output {disappeared_port!r} disappeared"
        elif selected_port and selected_port not in names:
            with self._status_lock:
                self._connection_state = MidiOutputConnectionState.UNAVAILABLE
                self._last_error = f"MIDI output {selected_port!r} is not currently available"
        elif not selected_port:
            with self._status_lock:
                self._connection_state = MidiOutputConnectionState.UNSELECTED
                self._last_error = None

    def _close_port(self) -> None:
        port = self._port
        self._port = None
        self._port_name = None
        if port is not None:
            with suppress(Exception):
                port.close()

    def _discard_desired_state(self) -> None:
        with self._condition:
            self._pending = None
            self._latest = None

    def _ensure_open(self) -> None:
        if self._closing or self._closed:
            raise RuntimeError("MIDI output service is closed")


__all__ = [
    "MidiMessage",
    "MidiMessageType",
    "MidiOutputBackend",
    "MidiOutputConfiguration",
    "MidiOutputPort",
    "MidiOutputService",
    "MidiOutputServiceFactory",
    "MidiOutputServiceProtocol",
    "MidiPortUnavailableError",
    "MidiServiceStatus",
    "MidoRtMidiBackend",
    "MockMidiOutputBackend",
    "MockMidiOutputPort",
    "VelocityUpdatePolicy",
]
