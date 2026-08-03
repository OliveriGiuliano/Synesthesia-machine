"""Phase 3 image-node facade retained while Phase 5 splits cohesive node families."""

from __future__ import annotations

from collections.abc import Mapping

from synesthesia_machine.contracts.runtime_values import (
    ColorSpace,
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import (
    convert_image,
)
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
)
from synesthesia_machine.nodes.image.adjustments import create_adjustment_definitions
from synesthesia_machine.nodes.image.channels import (
    ImageToLuminanceRuntime as ImageToLuminanceRuntime,
)
from synesthesia_machine.nodes.image.channels import (
    SeparateChannelsRuntime as SeparateChannelsRuntime,
)
from synesthesia_machine.nodes.image.channels import (
    create_channel_definitions,
)
from synesthesia_machine.nodes.image.dimensions import create_dimension_definitions
from synesthesia_machine.nodes.image.filters import create_filter_definitions
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    image_value,
    text_value,
)


class ChangeColourSpaceRuntime(StatelessImageRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        return {
            "image": convert_image(
                image_value(inputs["image"]),
                ColorSpace(text_value(parameters["target_colour_space"])),
            )
        }


def create_image_definitions() -> tuple[NodeDefinition, ...]:
    return (
        *create_dimension_definitions(),
        *create_adjustment_definitions(),
        *create_filter_definitions(),
        NodeDefinition(
            "synmachine.image.change_colour_space",
            1,
            "Change Colour Space",
            "Image / Utility",
            "Explicitly convert image colour values and descriptor metadata.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            (
                ParameterSpec(
                    "target_colour_space",
                    "Target colour space",
                    PortType.STRING,
                    ColorSpace.HSV.value,
                    choices=tuple(space.value for space in ColorSpace),
                ),
            ),
            ExecutionKind.STATELESS,
            ChangeColourSpaceRuntime,
            aliases=("convert colour", "convert color", "hsv", "lab", "ycrcb"),
        ),
        *create_channel_definitions(),
    )


__all__ = [
    "ChangeColourSpaceRuntime",
    "ImageToLuminanceRuntime",
    "SeparateChannelsRuntime",
    "create_image_definitions",
]
