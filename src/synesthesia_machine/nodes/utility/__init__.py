"""Built-in scalar, MIDI-state, and channel bridge utility nodes."""

from synesthesia_machine.nodes.utility.core import create_utility_registry
from synesthesia_machine.nodes.utility.dynamic import (
    create_difference_definitions,
    create_dynamic_definitions,
)
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

__all__ = [
    "MIDI_MERGE_TYPE_ID",
    "MULTIPLY_VELOCITY_TYPE_ID",
    "TRANSPOSE_TYPE_ID",
    "MidiMergeRuntime",
    "MultiplyVelocityRuntime",
    "TransposeRuntime",
    "create_difference_definitions",
    "create_dynamic_definitions",
    "create_midi_utility_definitions",
    "create_scalar_bridge_definitions",
    "create_utility_registry",
    "merge_midi_states",
    "multiply_velocity",
    "transpose_midi_state",
]
