"""Load Camera source definition; capture is owned by CameraSourceService."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts import (
    DeviceKind,
    FrameContext,
    NoData,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media.camera_source import CameraBackendPreference
from synesthesia_machine.nodes.base import (
    CachePolicy,
    ExecutionKind,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
)

LOAD_CAMERA_TYPE_ID = "synmachine.input.load_camera"


class LoadCameraRuntime:
    """Safe scheduler placeholder for externally injected live-camera outputs."""

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


def create_camera_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            LOAD_CAMERA_TYPE_ID,
            1,
            "Load Camera",
            "Input",
            "Brings in live video from a camera and reconnects if it drops out.",
            (),
            (
                OutputPortSpec("image", "Image", PortType.IMAGE),
                OutputPortSpec("processed_index", "Processed index", PortType.INT),
            ),
            (
                ParameterSpec(
                    "device_id",
                    "Camera device",
                    PortType.STRING,
                    "opencv:0",
                    help_text="Select a camera detected by the engine.",
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                    device_kind=DeviceKind.CAMERA_INPUT,
                ),
                ParameterSpec(
                    "requested_width",
                    "Requested width",
                    PortType.INT,
                    1280,
                    help_text=(
                        "Width the camera is asked to use; the camera may pick the closest "
                        "supported mode."
                    ),
                    minimum=1,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "requested_height",
                    "Requested height",
                    PortType.INT,
                    720,
                    help_text=(
                        "Height the camera is asked to use; the camera may pick the closest "
                        "supported mode."
                    ),
                    minimum=1,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "requested_fps",
                    "Requested FPS",
                    PortType.FLOAT,
                    30.0,
                    help_text=(
                        "Frame rate the camera is asked to use; it may pick the closest supported "
                        "rate."
                    ),
                    minimum=0.1,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "backend_preference",
                    "Camera compatibility",
                    PortType.STRING,
                    CameraBackendPreference.AUTO.value,
                    help_text=(
                        "Camera backends the engine is allowed to use; Automatic lets the engine "
                        "decide."
                    ),
                    choices=tuple(preference.value for preference in CameraBackendPreference),
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "process_every_nth_frame",
                    "Use every Nth frame",
                    PortType.INT,
                    1,
                    help_text="Uses every Nth captured frame; N=2 halves the processing load.",
                    minimum=1,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "reconnect_automatically",
                    "Reconnect automatically",
                    PortType.BOOL,
                    True,
                    help_text=(
                        "When on, a lost camera is restarted automatically instead of stopping "
                        "the node."
                    ),
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
            ),
            ExecutionKind.SOURCE,
            LoadCameraRuntime,
            cache_policy=CachePolicy.NEVER,
            aliases=("camera", "webcam", "live camera"),
        ),
    )


__all__ = ["LOAD_CAMERA_TYPE_ID", "LoadCameraRuntime", "create_camera_definitions"]
