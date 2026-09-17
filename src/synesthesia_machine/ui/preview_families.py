"""Preview-family presentation taxonomy.

Which preview dock a display visualizer feeds and the
one-visualizer-per-dock rule are headless metadata on the node definitions
(:mod:`synesthesia_machine.nodes.visualization`). This module owns the UI
side of each family: which canvas link pills render, which display
visualizer node a connection can be attached to, which window dock receives
its live previews, and which theme token colours its ports.

Every UI consumer that reacts to the payload type of a connection — the
canvas link pills, the image/note preview docks, the display visualizers a
connection can feed, and the port theme tokens — consults this module
instead of re-encoding the mapping as string literals. Adding a new payload
family is one entry in :data:`FAMILIES` (plus a theme token if it has one);
no consumer code changes.

The module is Qt-free: it maps type names to family identifiers, node type
ids, input port ids, and theme token names. Qt widgets (the docks
themselves) are resolved from the family's ``dock`` key by the window that
owns them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)

__all__ = [
    "FAMILIES",
    "THEME_TOKENS",
    "PreviewFamily",
    "PreviewFamilySpec",
    "display_visualizer",
    "pill_family",
    "pill_type_names",
    "preview_family",
    "theme_token",
]


class PreviewFamily(StrEnum):
    """A group of payload types that share one preview presentation."""

    SCALAR = "scalar"
    IMAGE = "image"
    CHANNEL = "channel"
    NOTE = "note"


@dataclass(frozen=True, slots=True)
class PreviewFamilySpec:
    """What one preview family brings to the UI.

    ``display_visualizer_type_id``/``visualizer_input_port`` name the display
    node whose input port receives the connection when the user asks to
    inspect it; ``dock`` is the window-owned dock key (``"image"`` or
    ``"note"``) that also receives live previews of this family. ``pill``
    marks families rendered as canvas link pills.
    """

    type_names: frozenset[str]
    display_visualizer_type_id: str | None = None
    visualizer_input_port: str | None = None
    dock: str | None = None
    pill: bool = True


FAMILIES: dict[PreviewFamily, PreviewFamilySpec] = {
    PreviewFamily.SCALAR: PreviewFamilySpec(frozenset({"INT", "FLOAT"})),
    PreviewFamily.IMAGE: PreviewFamilySpec(
        frozenset({"IMAGE"}),
        DISPLAY_IMAGE_DATA_TYPE_ID,
        "image",
        "image",
    ),
    PreviewFamily.CHANNEL: PreviewFamilySpec(
        frozenset({"CHANNEL"}),
        CHANNEL_DISPLAY_TYPE_ID,
        "channel",
        "image",
    ),
    PreviewFamily.NOTE: PreviewFamilySpec(
        frozenset({"MIDI_STATE"}),
        NOTE_VISUALIZER_TYPE_ID,
        "midi",
        "note",
        pill=False,
    ),
}

_TYPE_TO_FAMILY: dict[str, PreviewFamily] = {
    name: family for family, spec in FAMILIES.items() for name in spec.type_names
}

#: Port type names mapped to theme token names, not literal colours.
#: Every known payload type has a token; anything else falls back to
#: ``generic_port``.
THEME_TOKENS: dict[str, str] = {
    "FLOAT": "float_port",
    "INT": "int_port",
    "BOOL": "bool_port",
    "STRING": "string_port",
    "COLOR": "color_port",
    "IMAGE": "image_port",
    "CHANNEL": "channel_port",
    "MIDI_STATE": "midi_port",
}


def preview_family(type_name: str) -> PreviewFamily | None:
    """The preview family a resolved port type name belongs to, if any."""

    return _TYPE_TO_FAMILY.get(type_name)


def pill_family(type_name: str) -> PreviewFamily | None:
    """The pill family for a type name; ``None`` when no pill renders it."""

    family = _TYPE_TO_FAMILY.get(type_name)
    if family is None or not FAMILIES[family].pill:
        return None
    return family


def pill_type_names() -> frozenset[str]:
    """Every port type name that renders as a canvas link pill."""

    return frozenset(name for spec in FAMILIES.values() if spec.pill for name in spec.type_names)


def display_visualizer(type_name: str) -> tuple[str, str, str] | None:
    """``(visualizer type_id, input port, dock key)`` for a connection type.

    ``None`` for families without a display visualizer (e.g. scalars).
    """

    family = _TYPE_TO_FAMILY.get(type_name)
    if family is None:
        return None
    spec = FAMILIES[family]
    if spec.display_visualizer_type_id is None or spec.visualizer_input_port is None:
        return None
    return (spec.display_visualizer_type_id, spec.visualizer_input_port, spec.dock or "")


def theme_token(type_name: str) -> str:
    """Theme token name for a port type; ``generic_port`` for unknown types."""

    return THEME_TOKENS.get(type_name, "generic_port")
