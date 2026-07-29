"""Mockable MIDI output boundary with optional Mido/python-rtmidi enumeration."""

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Protocol, cast

import mido


@dataclass(frozen=True, slots=True)
class MidiMessage:
    type: str
    channel: int = 0
    note: int = 60
    velocity: int = 64


class MidiBackend(Protocol):
    def output_names(self) -> Sequence[str]: ...

    def send(self, port_name: str, message: MidiMessage) -> None: ...


class _MidoOutputPort(Protocol):
    def send(self, message: mido.Message) -> None: ...

    def __enter__(self) -> "_MidoOutputPort": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _MidoBackendFacade(Protocol):
    def get_output_names(self) -> Sequence[str]: ...

    def open_output(self, name: str) -> _MidoOutputPort: ...


def _empty_sent_messages() -> list[tuple[str, MidiMessage]]:
    return []


@dataclass(slots=True)
class MockMidiBackend:
    """Deterministic backend used by all automated tests."""

    ports: tuple[str, ...] = ("Mock MIDI Out",)
    sent: list[tuple[str, MidiMessage]] = field(default_factory=_empty_sent_messages)

    def output_names(self) -> Sequence[str]:
        return self.ports

    def send(self, port_name: str, message: MidiMessage) -> None:
        if port_name not in self.ports:
            msg = f"Unknown mock MIDI port: {port_name}"
            raise ValueError(msg)
        self.sent.append((port_name, message))


class MidoRtMidiBackend:
    """Real Windows MIDI backend; no port is opened during enumeration."""

    def __init__(self) -> None:
        self._backend = cast(
            _MidoBackendFacade,
            mido.Backend("mido.backends.rtmidi"),  # pyright: ignore[reportUnknownMemberType]
        )

    def output_names(self) -> Sequence[str]:
        return tuple(self._backend.get_output_names())

    def send(self, port_name: str, message: MidiMessage) -> None:
        mido_message = mido.Message(
            message.type,
            channel=message.channel,
            note=message.note,
            velocity=message.velocity,
        )
        with self._backend.open_output(port_name) as port:
            port.send(mido_message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="enumerate real output names")
    args = parser.parse_args()
    if not args.list:
        parser.error("use --list; sending real messages is intentionally not a default spike")
    print(json.dumps(list(MidoRtMidiBackend().output_names()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
