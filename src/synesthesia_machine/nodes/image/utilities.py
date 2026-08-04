"""Phase 5 image-utility node definitions and thin runtime adapters."""

from __future__ import annotations

from collections.abc import Mapping

from synesthesia_machine.contracts import (
    ColorSpace,
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media import convert_image
from synesthesia_machine.nodes import (
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
)
from synesthesia_machine.nodes.image.runtime_support import (
    StatelessImageRuntime,
    image_value,
    text_value,
)


class ChangeColourSpaceRuntime(StatelessImageRuntime):
    """Convert image values and descriptor metadata to an explicit colour space."""

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


def create_utility_definitions() -> tuple[NodeDefinition, ...]:
    """Return image-utility definitions in their persistent catalogue order."""

    return (
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
    )


__all__ = ["ChangeColourSpaceRuntime", "create_utility_definitions"]
