"""Output sink node definitions."""

from synesthesia_machine.midi import (
    DebugSynth,
    DebugSynthFactory,
    MidiOutputService,
    MidiOutputServiceFactory,
)
from synesthesia_machine.nodes.base import NodeDefinition
from synesthesia_machine.nodes.output.audio import (
    GENERATE_AUDIO_TYPE_ID,
    GenerateAudioRuntime,
)
from synesthesia_machine.nodes.output.audio import (
    create_output_definitions as create_audio_output_definitions,
)
from synesthesia_machine.nodes.output.midi import (
    SEND_MIDI_TYPE_ID,
    SendMidiRuntime,
    create_midi_output_definitions,
)


def create_output_definitions(
    *,
    synth_factory: DebugSynthFactory = DebugSynth,
    midi_output_service_factory: MidiOutputServiceFactory = MidiOutputService,
) -> tuple[NodeDefinition, ...]:
    return (
        *create_audio_output_definitions(synth_factory=synth_factory),
        *create_midi_output_definitions(service_factory=midi_output_service_factory),
    )


__all__ = [
    "GENERATE_AUDIO_TYPE_ID",
    "SEND_MIDI_TYPE_ID",
    "GenerateAudioRuntime",
    "SendMidiRuntime",
    "create_midi_output_definitions",
    "create_output_definitions",
]
