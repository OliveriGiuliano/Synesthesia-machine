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
from synesthesia_machine.nodes.synesthesia.fourier import (
    BAND_MEAN,
    BAND_PERCENTILE,
    FOURIER_TYPE_ID,
    HAMMING,
    HANN,
    HORIZONTAL,
    NONE,
    RADIAL_MAGNITUDE,
    VERTICAL,
    FourierBandMapKey,
    FourierRuntime,
    FourierShapeCache,
    build_fourier_band_map,
    create_fourier_definitions,
    fourier_band_amplitudes,
    fourier_to_midi_state,
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
        *create_fourier_definitions(),
    )


__all__ = [
    "AREA",
    "BAND_MEAN",
    "BAND_PERCENTILE",
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
    "FOURIER_TYPE_ID",
    "HAMMING",
    "HANN",
    "HORIZONTAL",
    "MAXIMUM",
    "MEAN",
    "NONE",
    "ORIENTATION",
    "PERIMETER",
    "PING_PONG",
    "RADIAL_MAGNITUDE",
    "SCANLINE_TYPE_ID",
    "TOP_TO_BOTTOM",
    "TREE",
    "VERTICAL",
    "ChannelToPitchRuntime",
    "CommonMusicalSettings",
    "ContourFeatures",
    "EdgesToPitchRuntime",
    "FourierBandMapKey",
    "FourierRuntime",
    "FourierShapeCache",
    "ScanlineRuntime",
    "build_fourier_band_map",
    "channel_histogram_to_midi_state",
    "common_musical_parameter_specs",
    "create_edges_to_pitch_definitions",
    "create_fourier_definitions",
    "create_scanline_definitions",
    "create_synesthesia_definitions",
    "edges_to_midi_state",
    "extract_contour_features",
    "fourier_band_amplitudes",
    "fourier_to_midi_state",
    "midi_state_from_candidates",
    "resolve_common_musical_settings",
    "scanline_to_midi_state",
    "validate_common_musical_parameters",
]
