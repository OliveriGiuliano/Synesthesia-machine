"""Image/channel-to-musical-state node definitions."""

from synesthesia_machine.nodes.synesthesia.channel_to_pitch import (
    CHANNEL_TO_PITCH_TYPE_ID,
    ChannelToPitchRuntime,
    channel_histogram_to_midi_state,
    create_synesthesia_definitions,
)
from synesthesia_machine.nodes.synesthesia.musical import (
    COMMON_MUSICAL_PARAMETER_GROUP,
    COMMON_MUSICAL_PARAMETER_IDS,
    CommonMusicalSettings,
    common_musical_parameter_specs,
    midi_state_from_candidates,
    resolve_common_musical_settings,
    validate_common_musical_parameters,
)

__all__ = [
    "CHANNEL_TO_PITCH_TYPE_ID",
    "COMMON_MUSICAL_PARAMETER_GROUP",
    "COMMON_MUSICAL_PARAMETER_IDS",
    "ChannelToPitchRuntime",
    "CommonMusicalSettings",
    "channel_histogram_to_midi_state",
    "common_musical_parameter_specs",
    "create_synesthesia_definitions",
    "midi_state_from_candidates",
    "resolve_common_musical_settings",
    "validate_common_musical_parameters",
]
