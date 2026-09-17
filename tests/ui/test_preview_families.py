"""Tests for the preview-family taxonomy.

The taxonomy is the single mapping from resolved port type names to preview
families, display visualizers, docks, and theme tokens; these tests pin the
whole table so a consumer can never drift from it.
"""

from __future__ import annotations

import pytest

from synesthesia_machine.nodes.visualization import (
    CHANNEL_DISPLAY_TYPE_ID,
    DISPLAY_IMAGE_DATA_TYPE_ID,
    NOTE_VISUALIZER_TYPE_ID,
)
from synesthesia_machine.ui.preview_families import (
    FAMILIES,
    PreviewFamily,
    display_visualizer,
    pill_family,
    pill_type_names,
    preview_family,
    theme_token,
    visualizer_type_ids,
)


def test_preview_family_covers_exactly_the_families() -> None:
    assert preview_family("IMAGE") is PreviewFamily.IMAGE
    assert preview_family("CHANNEL") is PreviewFamily.CHANNEL
    assert preview_family("INT") is PreviewFamily.SCALAR
    assert preview_family("FLOAT") is PreviewFamily.SCALAR
    assert preview_family("MIDI_STATE") is PreviewFamily.NOTE
    assert preview_family("BOOL") is None
    assert preview_family("STRING") is None
    assert preview_family("") is None


def test_type_names_partition_without_overlap() -> None:
    seen: dict[str, PreviewFamily] = {}
    for family, spec in FAMILIES.items():
        for name in spec.type_names:
            assert name not in seen, f"{name} claimed by {seen[name]} and {family}"
            seen[name] = family
    for name, family in seen.items():
        assert preview_family(name) is family


def test_every_family_type_name_resolves_to_its_family() -> None:
    for family, spec in FAMILIES.items():
        for name in spec.type_names:
            assert preview_family(name) is family


def test_pill_families_exclude_note_and_unknown_types() -> None:
    assert pill_family("IMAGE") is PreviewFamily.IMAGE
    assert pill_family("CHANNEL") is PreviewFamily.CHANNEL
    assert pill_family("INT") is PreviewFamily.SCALAR
    assert pill_family("MIDI_STATE") is None
    assert pill_family("BOOL") is None


def test_pill_type_names_match_the_pill_families() -> None:
    assert pill_type_names() == frozenset({"IMAGE", "CHANNEL", "INT", "FLOAT"})


def test_display_visualizer_targets_are_the_family_visualizers() -> None:
    assert display_visualizer("IMAGE") == (DISPLAY_IMAGE_DATA_TYPE_ID, "image", "image")
    assert display_visualizer("CHANNEL") == (CHANNEL_DISPLAY_TYPE_ID, "channel", "image")
    assert display_visualizer("MIDI_STATE") == (NOTE_VISUALIZER_TYPE_ID, "midi", "note")
    assert display_visualizer("FLOAT") is None
    assert display_visualizer("BOOL") is None


def test_visualizer_type_ids_are_grouped_by_dock() -> None:
    assert visualizer_type_ids("image") == frozenset(
        {DISPLAY_IMAGE_DATA_TYPE_ID, CHANNEL_DISPLAY_TYPE_ID}
    )
    assert visualizer_type_ids("note") == frozenset({NOTE_VISUALIZER_TYPE_ID})
    assert visualizer_type_ids("unknown dock") == frozenset()


def test_theme_tokens_cover_all_port_types_with_a_generic_fallback() -> None:
    assert theme_token("FLOAT") == "float_port"
    assert theme_token("INT") == "int_port"
    assert theme_token("BOOL") == "bool_port"
    assert theme_token("STRING") == "string_port"
    assert theme_token("COLOR") == "color_port"
    assert theme_token("IMAGE") == "image_port"
    assert theme_token("CHANNEL") == "channel_port"
    assert theme_token("MIDI_STATE") == "midi_port"
    assert theme_token("UNKNOWNTYPE") == "generic_port"


@pytest.mark.parametrize(
    ("type_name", "family"),
    [
        ("IMAGE", PreviewFamily.IMAGE),
        ("CHANNEL", PreviewFamily.CHANNEL),
        ("INT", PreviewFamily.SCALAR),
        ("FLOAT", PreviewFamily.SCALAR),
        ("MIDI_STATE", PreviewFamily.NOTE),
    ],
)
def test_family_values_are_stable_strings(type_name: str, family: PreviewFamily) -> None:
    assert preview_family(type_name) is family
    assert family.value in ("scalar", "image", "channel", "note")
