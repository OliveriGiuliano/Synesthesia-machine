"""Image/channel-to-musical-state node definitions."""

from synesthesia_machine.nodes import NodeDefinition
from synesthesia_machine.nodes.synesthesia.channel_to_pitch import (
    CHANNEL_TO_PITCH_TYPE_ID,
    ChannelToPitchRuntime,
    channel_histogram_to_midi_state,
)
from synesthesia_machine.nodes.synesthesia.channel_to_pitch import (
    create_synesthesia_definitions as create_channel_to_pitch_definitions,
)
from synesthesia_machine.nodes.synesthesia.edges_to_pitch import (
    AREA,
    CENTROID_X,
    CENTROID_Y,
    CIRCULARITY,
    EDGE_STRENGTH,
    EDGES_TO_PITCH_TYPE_ID,
    EXTERNAL,
    ORIENTATION,
    PERIMETER,
    TREE,
    ContourFeatures,
    EdgesToPitchRuntime,
    create_edges_to_pitch_definitions,
    edges_to_midi_state,
    extract_contour_features,
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
from synesthesia_machine.nodes.synesthesia.scanline import (
    BOTTOM_TO_TOP,
    MAXIMUM,
    MEAN,
    PING_PONG,
    SCANLINE_TYPE_ID,
    TOP_TO_BOTTOM,
    ScanlineRuntime,
    create_scanline_definitions,
    scanline_to_midi_state,
)


def create_synesthesia_definitions() -> tuple[NodeDefinition, ...]:
    """Return the current ordered visual-to-musical node catalogue."""

    return (
        *create_channel_to_pitch_definitions(),
        *create_scanline_definitions(),
        *create_edges_to_pitch_definitions(),
    )


__all__ = [
    "AREA",
    "BOTTOM_TO_TOP",
    "CENTROID_X",
    "CENTROID_Y",
    "CHANNEL_TO_PITCH_TYPE_ID",
    "CIRCULARITY",
    "COMMON_MUSICAL_PARAMETER_GROUP",
    "COMMON_MUSICAL_PARAMETER_IDS",
    "EDGES_TO_PITCH_TYPE_ID",
    "EDGE_STRENGTH",
    "EXTERNAL",
    "MAXIMUM",
    "MEAN",
    "ORIENTATION",
    "PERIMETER",
    "PING_PONG",
    "SCANLINE_TYPE_ID",
    "TOP_TO_BOTTOM",
    "TREE",
    "ChannelToPitchRuntime",
    "CommonMusicalSettings",
    "ContourFeatures",
    "EdgesToPitchRuntime",
    "ScanlineRuntime",
    "channel_histogram_to_midi_state",
    "common_musical_parameter_specs",
    "create_edges_to_pitch_definitions",
    "create_scanline_definitions",
    "create_synesthesia_definitions",
    "edges_to_midi_state",
    "extract_contour_features",
    "midi_state_from_candidates",
    "resolve_common_musical_settings",
    "scanline_to_midi_state",
    "validate_common_musical_parameters",
]
