"""Built-in image catalogue architecture and ordering checks."""

from pathlib import Path

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.nodes.image import create_image_definitions
from synesthesia_machine.nodes.image.catalogue import (
    create_image_definitions as create_catalogue_definitions,
)
from synesthesia_machine.nodes.image.core import (
    ChangeColourSpaceRuntime as FacadeChangeColourSpaceRuntime,
)
from synesthesia_machine.nodes.image.core import (
    create_image_definitions as create_facade_definitions,
)
from synesthesia_machine.nodes.image.utilities import (
    ChangeColourSpaceRuntime,
    create_utility_definitions,
)
from synesthesia_machine.persistence import load_graph

EXPECTED_IMAGE_TYPE_IDS = (
    "synmachine.image.resize",
    "synmachine.image.crop",
    "synmachine.image.flip",
    "synmachine.image.rotate",
    "synmachine.image.brightness",
    "synmachine.image.contrast",
    "synmachine.image.clamp",
    "synmachine.image.colour_levels",
    "synmachine.image.hue",
    "synmachine.image.saturation",
    "synmachine.image.invert_colour",
    "synmachine.image.stretch_contrast",
    "synmachine.image.gamma",
    "synmachine.image.add_scalar",
    "synmachine.image.multiply_scalar",
    "synmachine.image.divide_scalar",
    "synmachine.image.gaussian_blur",
    "synmachine.image.sharpen",
    "synmachine.image.add_noise",
    "synmachine.image.posterize",
    "synmachine.image.threshold",
    "synmachine.image.canny",
    "synmachine.image.convolve",
    "synmachine.image.dilate",
    "synmachine.image.erode",
    "synmachine.image.high_pass",
    "synmachine.image.low_pass",
    "synmachine.image.difference",
    "synmachine.image.change_colour_space",
    "synmachine.image.blend_images",
    "synmachine.image.separate_channels",
    "synmachine.image.combine_channels",
    "synmachine.image.to_luminance",
    "synmachine.image.posterize_time",
    "synmachine.image.hold_image",
)
COMPATIBILITY_CATALOGUE_PATH = Path(
    "tests/fixtures/compatibility/catalogues/catalogue-50-definitions.synmachine.json"
)


def test_image_catalogue_has_exact_stable_order_and_unique_type_ids() -> None:
    definitions = create_image_definitions()
    type_ids = tuple(definition.type_id for definition in definitions)

    assert type_ids == EXPECTED_IMAGE_TYPE_IDS
    assert len(type_ids) == len(set(type_ids)) == 35
    assert tuple(definition.type_id for definition in create_catalogue_definitions()) == type_ids


def test_image_core_facade_preserves_runtime_and_catalogue_imports() -> None:
    assert FacadeChangeColourSpaceRuntime is ChangeColourSpaceRuntime
    assert create_facade_definitions is create_catalogue_definitions
    assert create_image_definitions is create_catalogue_definitions
    assert tuple(definition.type_id for definition in create_utility_definitions()) == (
        "synmachine.image.change_colour_space",
    )


def test_builtin_registry_preserves_compatibility_catalogue_as_50_definition_subset() -> None:
    registry = create_application_registry()
    definitions = registry.definitions()
    type_ids = tuple(definition.type_id for definition in definitions)
    compatibility_ids = {
        node.type_id for node in load_graph(COMPATIBILITY_CATALOGUE_PATH, registry).nodes
    }

    assert len(type_ids) == len(set(type_ids))
    # The historical catalogue holds 50 nodes; the v3-to-v4 graph migration
    # drops the retired opacity node, so loading yields 49 unique type IDs.
    assert len(compatibility_ids) == 49
    assert "synmachine.image.opacity" not in compatibility_ids
    assert compatibility_ids <= set(type_ids)
    assert tuple(type_id for type_id in type_ids if type_id.startswith("synmachine.image.")) == (
        tuple(sorted(EXPECTED_IMAGE_TYPE_IDS))
    )
