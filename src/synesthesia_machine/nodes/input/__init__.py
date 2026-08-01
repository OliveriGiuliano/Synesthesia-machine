"""Built-in file and live media input node definitions."""

from synesthesia_machine.nodes.base import NodeDefinition
from synesthesia_machine.nodes.input.camera import (
    LOAD_CAMERA_TYPE_ID,
    create_camera_definitions,
)
from synesthesia_machine.nodes.input.video import LOAD_VIDEO_TYPE_ID
from synesthesia_machine.nodes.input.video import (
    create_input_definitions as create_video_definitions,
)


def create_input_definitions() -> tuple[NodeDefinition, ...]:
    return (*create_video_definitions(), *create_camera_definitions())


__all__ = ["LOAD_CAMERA_TYPE_ID", "LOAD_VIDEO_TYPE_ID", "create_input_definitions"]
