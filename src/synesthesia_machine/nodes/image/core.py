"""Import-compatible facade for the split image catalogue."""

from synesthesia_machine.nodes.image.catalogue import (
    create_image_definitions as create_image_definitions,
)
from synesthesia_machine.nodes.image.channels import (
    ImageToLuminanceRuntime as ImageToLuminanceRuntime,
)
from synesthesia_machine.nodes.image.channels import (
    SeparateChannelsRuntime as SeparateChannelsRuntime,
)
from synesthesia_machine.nodes.image.utilities import (
    ChangeColourSpaceRuntime as ChangeColourSpaceRuntime,
)

__all__ = [
    "ChangeColourSpaceRuntime",
    "ImageToLuminanceRuntime",
    "SeparateChannelsRuntime",
    "create_image_definitions",
]
