"""Load Video source definition; lifecycle execution is owned by VideoSourceService."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.nodes.base import (
    CachePolicy,
    ExecutionKind,
    NodeDefinition,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
)

LOAD_VIDEO_TYPE_ID = "synmachine.input.load_video"


class LoadVideoRuntime:
    """Safe scheduler placeholder for externally injected source outputs."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {"image": NoData, "processed_index": NoData}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def create_input_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            LOAD_VIDEO_TYPE_ID,
            1,
            "Load Video",
            "Input",
            "Decode and present a saved video using its PyAV presentation timestamps.",
            (),
            (
                OutputPortSpec("image", "Image", PortType.IMAGE),
                OutputPortSpec("processed_index", "Processed index", PortType.INT),
            ),
            (
                ParameterSpec(
                    "file_path",
                    "File path",
                    PortType.STRING,
                    "",
                    help_text="Path to a saved video file.",
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "playback_speed",
                    "Playback speed",
                    PortType.FLOAT,
                    1.0,
                    help_text=(
                        "Scale PTS playback timing from 0.25x to 4x; changing it restarts the "
                        "source."
                    ),
                    minimum=0.25,
                    maximum=4.0,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "process_every_nth_frame",
                    "Process every Nth frame",
                    PortType.INT,
                    1,
                    minimum=1,
                    help_text="N=2 processes source frames 1, 3, 5… without slowing playback.",
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "loop",
                    "Loop",
                    PortType.BOOL,
                    False,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "stream_index",
                    "Video stream index",
                    PortType.INT,
                    0,
                    minimum=0,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
            ),
            ExecutionKind.SOURCE,
            LoadVideoRuntime,
            cache_policy=CachePolicy.NEVER,
            aliases=("video", "movie", "file video"),
        ),
    )


__all__ = ["LOAD_VIDEO_TYPE_ID", "LoadVideoRuntime", "create_input_definitions"]
