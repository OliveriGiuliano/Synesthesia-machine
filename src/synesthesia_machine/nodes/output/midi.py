"""Nonblocking Send MIDI sink backed by a persistent child-owned output service."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    MidiOutputConnectionState,
    MidiOutputStatus,
    MidiStateFrame,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.midi.output_service import (
    MidiOutputConfiguration,
    MidiOutputService,
    MidiOutputServiceFactory,
    MidiOutputServiceProtocol,
    VelocityUpdatePolicy,
)
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
)

SEND_MIDI_TYPE_ID = "synmachine.output.send_midi"


class SendMidiRuntime:
    def __init__(
        self,
        node_id: UUID,
        *,
        service_factory: MidiOutputServiceFactory = MidiOutputService,
    ) -> None:
        self.node_id = node_id
        self._service: MidiOutputServiceProtocol = service_factory()
        self._input_missing = False

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        midi = inputs["midi"]
        if midi is NoData:
            if not self._input_missing:
                self._service.request_panic()
                self._input_missing = True
            return {}
        if not isinstance(midi, MidiStateFrame):
            raise TypeError("Send MIDI requires MIDI state input")
        self._input_missing = False
        configuration = MidiOutputConfiguration(
            output_port=_text(parameters["output_port"]),
            velocity_policy=VelocityUpdatePolicy(_text(parameters["velocity_update_policy"])),
            velocity_change_threshold=_integer(parameters["velocity_change_threshold"]),
        )
        self._service.publish(midi, configuration)
        status = self._service.status()
        if configuration.output_port and status.connection_state in {
            MidiOutputConnectionState.ERROR,
            MidiOutputConnectionState.UNAVAILABLE,
        }:
            raise ExpectedNodeError(
                "midi_output_unavailable",
                status.last_error or f"MIDI output {configuration.output_port!r} is unavailable",
            )
        return {}

    def midi_output_status(self) -> MidiOutputStatus:
        status = self._service.status()
        return MidiOutputStatus(
            self.node_id,
            status.connection_state,
            status.selected_port,
            status.available_ports,
            status.active_note_count,
            status.active_channels,
            status.dropped_state_updates,
            status.last_error,
        )

    def panic(self) -> None:
        self._service.panic()

    def reset(self, reason: ResetReason) -> None:
        del reason
        self._service.panic()
        self._input_missing = False

    def close(self) -> None:
        self._service.close()


def create_midi_output_definitions(
    *, service_factory: MidiOutputServiceFactory = MidiOutputService
) -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            SEND_MIDI_TYPE_ID,
            1,
            "Send MIDI to MIDI Output",
            "Output / MIDI",
            "Send complete desired MIDI note state to one explicitly selected output.",
            (InputPortSpec("midi", "MIDI State", PortType.MIDI_STATE),),
            (),
            (
                ParameterSpec(
                    "output_port",
                    "MIDI output port",
                    PortType.STRING,
                    "",
                    help_text=(
                        "Exact enumerated port name. Empty opens no port; missing names are never "
                        "replaced automatically."
                    ),
                    update_mode=ParameterUpdateMode.RECOMPILE,
                ),
                ParameterSpec(
                    "velocity_update_policy",
                    "Velocity update policy",
                    PortType.STRING,
                    VelocityUpdatePolicy.IGNORE_WHILE_HELD.value,
                    choices=tuple(policy.value for policy in VelocityUpdatePolicy),
                ),
                ParameterSpec(
                    "velocity_change_threshold",
                    "Velocity change threshold",
                    PortType.INT,
                    4,
                    minimum=0,
                    maximum=127,
                ),
            ),
            ExecutionKind.SINK,
            partial(SendMidiRuntime, service_factory=service_factory),
            cache_policy=CachePolicy.NEVER,
            handles_no_data=True,
            aliases=("midi output", "send notes", "rtmidi"),
        ),
    )


def _integer(value: ParameterValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("Expected integer parameter")
    return value


def _text(value: ParameterValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected text parameter")
    return value


__all__ = ["SEND_MIDI_TYPE_ID", "SendMidiRuntime", "create_midi_output_definitions"]
