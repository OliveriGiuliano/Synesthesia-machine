"""Built-in scalar, MIDI-state, and channel bridge utility nodes."""

from synesthesia_machine.nodes.utility.analysis import create_analysis_definitions
from synesthesia_machine.nodes.utility.core import create_utility_registry
from synesthesia_machine.nodes.utility.midi import (
    MIDI_MERGE_TYPE_ID,
    MULTIPLY_VELOCITY_TYPE_ID,
    TRANSPOSE_TYPE_ID,
    MidiMergeRuntime,
    MultiplyVelocityRuntime,
    TransposeRuntime,
    create_midi_utility_definitions,
    merge_midi_states,
    multiply_velocity,
    transpose_midi_state,
)
from synesthesia_machine.nodes.utility.scalar_bridges import create_scalar_bridge_definitions
from synesthesia_machine.nodes.utility.temporal import create_temporal_definitions
from synesthesia_machine.nodes.utility.transform import create_transform_definitions

__all__ = [
    "MIDI_MERGE_TYPE_ID",
    "MULTIPLY_VELOCITY_TYPE_ID",
    "TRANSPOSE_TYPE_ID",
    "MidiMergeRuntime",
    "MultiplyVelocityRuntime",
    "TransposeRuntime",
    "create_analysis_definitions",
    "create_midi_utility_definitions",
    "create_scalar_bridge_definitions",
    "create_temporal_definitions",
    "create_transform_definitions",
    "create_utility_registry",
    "merge_midi_states",
    "multiply_velocity",
    "transpose_midi_state",
]
