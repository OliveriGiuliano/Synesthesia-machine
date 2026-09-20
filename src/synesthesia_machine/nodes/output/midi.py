"""Nonblocking Send MIDI sink backed by a persistent child-owned output service."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import cast
from uuid import UUID

from synesthesia_machine.contracts import (
    DeviceKind,
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
    NodeExecutionContract,
    NodePresentationIntent,
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
        midi = inputs["midi"]
        if midi is NoData:
            if not self._input_missing:
                # The missing input stops the source: panic with this tick's
                # generation so a late state from it cannot re-arm the port.
                self._service.request_panic(context.publish_generation)
                self._input_missing = True
            return {}
        self._input_missing = False
        configuration = MidiOutputConfiguration(
            output_port=cast(str, parameters["output_port"]),
            velocity_policy=VelocityUpdatePolicy(cast(str, parameters["velocity_update_policy"])),
            velocity_change_threshold=cast(int, parameters["velocity_change_threshold"]),
        )
        self._service.publish(cast(MidiStateFrame, midi), configuration)
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

    def panic(self, publish_generation: int) -> None:
        self._service.panic(publish_generation)

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
            execution=NodeExecutionContract(
                SEND_MIDI_TYPE_ID,
                1,
                ExecutionKind.SINK,
                (InputPortSpec("midi", "MIDI State", PortType.MIDI_STATE),),
                (),
                (
                    ParameterSpec(
                        "output_port",
                        "MIDI output port",
                        PortType.STRING,
                        "",
                        help_text=(
                            "Select a MIDI output detected by the engine. No output keeps this "
                            "node "
                            "silent."
                        ),
                        update_mode=ParameterUpdateMode.RECOMPILE,
                        device_kind=DeviceKind.MIDI_OUTPUT,
                    ),
                    ParameterSpec(
                        "velocity_update_policy",
                        "Velocity update policy",
                        PortType.STRING,
                        VelocityUpdatePolicy.IGNORE_WHILE_HELD.value,
                        help_text=(
                            "How a velocity change is delivered for a note that is already "
                            "sounding: "
                            "Ignore keeps the original velocity, Retrigger releases the note and "
                            "presses it again, and Repeat note-on sends another note-on message."
                        ),
                        choices=tuple(policy.value for policy in VelocityUpdatePolicy),
                    ),
                    ParameterSpec(
                        "velocity_change_threshold",
                        "Velocity change threshold",
                        PortType.INT,
                        4,
                        help_text=(
                            "A note is only re-sent when its velocity changes by at least this "
                            "much."
                        ),
                        minimum=0,
                        maximum=127,
                    ),
                ),
                partial(SendMidiRuntime, service_factory=service_factory),
                cache_policy=CachePolicy.NEVER,
                handles_no_data=True,
            ),
            presentation=NodePresentationIntent(
                "Send MIDI",
                "Output / MIDI",
                "Sends the notes to a MIDI device such as a synth or a music program.",
                aliases=("midi output", "send notes", "rtmidi"),
            ),
        ),
    )


__all__ = ["SEND_MIDI_TYPE_ID", "SendMidiRuntime", "create_midi_output_definitions"]
