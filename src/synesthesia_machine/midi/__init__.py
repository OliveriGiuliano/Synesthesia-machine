"""Qt-free musical selection and debug-output services."""

from synesthesia_machine.midi.debug_synth import (
    DebugSynth,
    DebugSynthFactory,
    DebugSynthService,
    SynthConfiguration,
    SynthWaveform,
)
from synesthesia_machine.midi.scales import (
    BUILTIN_SCALE_REGISTRY,
    CUSTOM_SCALE_ID,
    PITCH_CLASS_NAMES,
    MusicalSelector,
    ScaleDefinition,
    ScaleRegistry,
    parse_custom_pitch_class_mask,
    resolve_musical_selector,
    select_midi_notes,
)

__all__ = [
    "BUILTIN_SCALE_REGISTRY",
    "CUSTOM_SCALE_ID",
    "PITCH_CLASS_NAMES",
    "DebugSynth",
    "DebugSynthFactory",
    "DebugSynthService",
    "MusicalSelector",
    "ScaleDefinition",
    "ScaleRegistry",
    "SynthConfiguration",
    "SynthWaveform",
    "parse_custom_pitch_class_mask",
    "resolve_musical_selector",
    "select_midi_notes",
]
