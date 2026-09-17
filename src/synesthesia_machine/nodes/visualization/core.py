"""Lightweight visualization nodes whose compact payloads are published by the engine."""

from __future__ import annotations

from collections.abc import Mapping

from synesthesia_machine.contracts.runtime_values import (
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import FitMode
from synesthesia_machine.nodes.base import (
    CachePolicy,
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    ParameterSpec,
    StatelessRuntime,
)
from synesthesia_machine.nodes.migrations import (
    migrate_channel_display_v1_to_v2,
    migrate_display_image_data_v1_to_v2,
)

DISPLAY_IMAGE_DATA_TYPE_ID = "synmachine.visualization.display_image_data"
CHANNEL_DISPLAY_TYPE_ID = "synmachine.visualization.channel_display"
NOTE_VISUALIZER_TYPE_ID = "synmachine.visualization.note_visualizer"


class _VisualizerRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {}


def create_visualization_definitions() -> tuple[NodeDefinition, ...]:
    """Return permanent visualizer definitions in stable display order."""

    return (
        NodeDefinition(
            DISPLAY_IMAGE_DATA_TYPE_ID,
            2,
            "Display Image Data",
            "Visualization",
            "Shows an image as a preview on your screen.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (),
            (
                ParameterSpec(
                    "fit_mode",
                    "Fit mode",
                    PortType.STRING,
                    FitMode.CONTAIN.value,
                    help_text=(
                        "Chooses how the picture fits the panel: Stretch fills it exactly, "
                        "Contain fits it inside with letterboxing, and Cover fills it and crops "
                        "the overflow."
                    ),
                    choices=tuple(mode.value for mode in FitMode),
                ),
                ParameterSpec(
                    "checkerboard_alpha",
                    "Checkerboard alpha",
                    PortType.BOOL,
                    True,
                    help_text="Draws a checkerboard behind transparent areas of the image.",
                ),
                ParameterSpec(
                    "value_display_mode",
                    "Value display",
                    PortType.STRING,
                    "DISPLAY_TRANSFORM",
                    help_text="Chooses how the image values are converted to colours for display.",
                    choices=("DISPLAY_TRANSFORM",),
                ),
                ParameterSpec(
                    "show_histogram",
                    "Show histogram",
                    PortType.BOOL,
                    False,
                    help_text="Shows a histogram of the image values below the picture.",
                ),
            ),
            ExecutionKind.VISUALIZER,
            _VisualizerRuntime,
            cache_policy=CachePolicy.NEVER,
            aliases=("image preview", "view image", "monitor image"),
            migrations={1: migrate_display_image_data_v1_to_v2},
        ),
        NodeDefinition(
            CHANNEL_DISPLAY_TYPE_ID,
            2,
            "Channel Display",
            "Visualization",
            "Shows a channel as a preview on your screen.",
            (InputPortSpec("channel", "Channel", PortType.CHANNEL),),
            (),
            (
                ParameterSpec(
                    "fit_mode",
                    "Fit mode",
                    PortType.STRING,
                    FitMode.CONTAIN.value,
                    help_text=(
                        "Chooses how the picture fits the panel: Stretch fills it exactly, "
                        "Contain fits it inside with letterboxing, and Cover fills it and crops "
                        "the overflow."
                    ),
                    choices=tuple(mode.value for mode in FitMode),
                ),
                ParameterSpec(
                    "value_display_mode",
                    "Value display",
                    PortType.STRING,
                    "NOMINAL_RANGE",
                    help_text=(
                        "Chooses how the channel values are converted to colours; only the "
                        "nominal range mode is available."
                    ),
                    choices=("NOMINAL_RANGE",),
                ),
                ParameterSpec(
                    "show_histogram",
                    "Show histogram",
                    PortType.BOOL,
                    False,
                    help_text="Shows a histogram of the channel values below the picture.",
                ),
            ),
            ExecutionKind.VISUALIZER,
            _VisualizerRuntime,
            cache_policy=CachePolicy.NEVER,
            aliases=("channel preview", "view channel", "monitor channel"),
            migrations={1: migrate_channel_display_v1_to_v2},
        ),
        NodeDefinition(
            NOTE_VISUALIZER_TYPE_ID,
            1,
            "Note Visualizer",
            "Visualization",
            "Shows the notes the graph is playing right now.",
            (InputPortSpec("midi", "MIDI State", PortType.MIDI_STATE),),
            (),
            (),
            ExecutionKind.VISUALIZER,
            _VisualizerRuntime,
            cache_policy=CachePolicy.NEVER,
            aliases=("midi preview", "piano", "notes"),
        ),
    )


__all__ = [
    "CHANNEL_DISPLAY_TYPE_ID",
    "DISPLAY_IMAGE_DATA_TYPE_ID",
    "NOTE_VISUALIZER_TYPE_ID",
    "create_visualization_definitions",
]
