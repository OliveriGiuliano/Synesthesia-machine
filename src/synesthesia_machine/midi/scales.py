"""Authoritative pitch-class scales and deterministic musical-note selection."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from synesthesia_machine.contracts import MidiNoteKey

PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
CUSTOM_SCALE_ID = "custom"
_SCALE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def _mask(*pitch_classes: int) -> tuple[bool, ...]:
    selected = frozenset(pitch_classes)
    return tuple(pitch_class in selected for pitch_class in range(12))


@dataclass(frozen=True, slots=True)
class ScaleDefinition:
    id: str
    display_name: str
    pitch_class_mask: tuple[bool, ...]

    def __post_init__(self) -> None:
        if not _SCALE_ID.fullmatch(self.id):
            raise ValueError(f"Invalid scale ID: {self.id!r}")
        if not self.display_name.strip():
            raise ValueError("Scale display_name cannot be empty")
        if len(self.pitch_class_mask) != 12:
            raise ValueError("Scale pitch_class_mask must contain exactly 12 values")


class ScaleRegistry:
    """Immutable stable-ID lookup for the built-in musical scales."""

    def __init__(self, definitions: Iterable[ScaleDefinition]) -> None:
        by_id: dict[str, ScaleDefinition] = {}
        for definition in definitions:
            if definition.id in by_id:
                raise ValueError(f"Duplicate scale ID: {definition.id!r}")
            by_id[definition.id] = definition
        if not by_id:
            raise ValueError("Scale registry cannot be empty")
        self._by_id: Mapping[str, ScaleDefinition] = MappingProxyType(by_id)

    def __iter__(self) -> Iterator[ScaleDefinition]:
        return iter(self._by_id.values())

    def get(self, scale_id: str) -> ScaleDefinition | None:
        return self._by_id.get(scale_id)

    def require(self, scale_id: str) -> ScaleDefinition:
        definition = self.get(scale_id)
        if definition is None:
            raise ValueError(f"Unknown scale ID: {scale_id!r}")
        return definition

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)


BUILTIN_SCALE_REGISTRY = ScaleRegistry(
    (
        ScaleDefinition("chromatic", "Chromatic", _mask(*range(12))),
        ScaleDefinition("major", "Major / Ionian", _mask(0, 2, 4, 5, 7, 9, 11)),
        ScaleDefinition("natural_minor", "Natural Minor / Aeolian", _mask(0, 2, 3, 5, 7, 8, 10)),
        ScaleDefinition("harmonic_minor", "Harmonic Minor", _mask(0, 2, 3, 5, 7, 8, 11)),
        ScaleDefinition("melodic_minor", "Melodic Minor", _mask(0, 2, 3, 5, 7, 9, 11)),
        ScaleDefinition("dorian", "Dorian", _mask(0, 2, 3, 5, 7, 9, 10)),
        ScaleDefinition("phrygian", "Phrygian", _mask(0, 1, 3, 5, 7, 8, 10)),
        ScaleDefinition("lydian", "Lydian", _mask(0, 2, 4, 6, 7, 9, 11)),
        ScaleDefinition("mixolydian", "Mixolydian", _mask(0, 2, 4, 5, 7, 9, 10)),
        ScaleDefinition("locrian", "Locrian", _mask(0, 1, 3, 5, 6, 8, 10)),
        ScaleDefinition("major_pentatonic", "Major Pentatonic", _mask(0, 2, 4, 7, 9)),
        ScaleDefinition("minor_pentatonic", "Minor Pentatonic", _mask(0, 3, 5, 7, 10)),
        ScaleDefinition("whole_tone", "Whole Tone", _mask(0, 2, 4, 6, 8, 10)),
        ScaleDefinition("diminished", "Diminished", _mask(0, 2, 3, 5, 6, 8, 9, 11)),
        ScaleDefinition(CUSTOM_SCALE_ID, "Custom 12-step mask", _mask(*range(12))),
    )
)


def parse_custom_pitch_class_mask(value: str) -> tuple[bool, ...]:
    """Parse the persisted custom scale format: exactly twelve ``0``/``1`` characters."""

    if len(value) != 12 or any(character not in "01" for character in value):
        raise ValueError("Custom scale mask must contain exactly twelve 0 or 1 characters")
    return tuple(character == "1" for character in value)


@dataclass(frozen=True, slots=True)
class MusicalSelector:
    root_pitch_class: int
    scale: ScaleDefinition
    midi_minimum: int
    midi_maximum: int

    def __post_init__(self) -> None:
        if not 0 <= self.root_pitch_class <= 11:
            raise ValueError("root_pitch_class must be in the range 0..11")
        if not 0 <= self.midi_minimum <= 127 or not 0 <= self.midi_maximum <= 127:
            raise ValueError("MIDI selector bounds must be in the range 0..127")
        if self.midi_minimum > self.midi_maximum:
            raise ValueError("MIDI minimum cannot exceed MIDI maximum")
        if not self.allowed_notes:
            raise ValueError("Musical selector does not allow any notes in the MIDI range")

    @property
    def allowed_notes(self) -> tuple[int, ...]:
        mask = self.scale.pitch_class_mask
        root = self.root_pitch_class
        return tuple(
            note
            for note in range(self.midi_minimum, self.midi_maximum + 1)
            if mask[(note - root) % 12]
        )


def resolve_musical_selector(
    root_pitch_class: str,
    scale_id: str,
    midi_minimum: int,
    midi_maximum: int,
    *,
    custom_pitch_class_mask: str = "111111111111",
) -> MusicalSelector:
    """Resolve persisted selector values into one validated ordered-note selector."""

    try:
        root = PITCH_CLASS_NAMES.index(root_pitch_class)
    except ValueError as error:
        raise ValueError(f"Unknown root pitch class: {root_pitch_class!r}") from error
    scale = BUILTIN_SCALE_REGISTRY.require(scale_id)
    if scale.id == CUSTOM_SCALE_ID:
        scale = ScaleDefinition(
            CUSTOM_SCALE_ID,
            scale.display_name,
            parse_custom_pitch_class_mask(custom_pitch_class_mask),
        )
    return MusicalSelector(root, scale, midi_minimum, midi_maximum)


def select_midi_notes(
    candidates: Iterable[tuple[int, float]],
    *,
    channel: int,
    maximum_polyphony: int,
    minimum_velocity: int,
    maximum_velocity: int,
) -> Mapping[MidiNoteKey, int]:
    """Merge, rank, limit, and velocity-map normalized candidate note strengths."""

    if not 0 <= channel <= 15:
        raise ValueError("MIDI channel must be in the range 0..15")
    if maximum_polyphony < 1:
        raise ValueError("maximum_polyphony must be at least 1")
    if not 1 <= minimum_velocity <= maximum_velocity <= 127:
        raise ValueError("Velocity range must satisfy 1 <= minimum <= maximum <= 127")

    strengths: dict[int, float] = {}
    for note, strength in candidates:
        if not 0 <= note <= 127:
            raise ValueError("Candidate MIDI note must be in the range 0..127")
        if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
            raise ValueError("Candidate strength must be finite and normalized to 0..1")
        strengths[note] = max(strengths.get(note, 0.0), strength)

    strongest = sorted(strengths.items(), key=lambda item: (-item[1], item[0]))[:maximum_polyphony]
    velocity_span = maximum_velocity - minimum_velocity
    selected = {
        MidiNoteKey(channel, note): min(
            maximum_velocity,
            minimum_velocity + math.floor(strength * velocity_span + 0.5),
        )
        for note, strength in strongest
    }
    return MappingProxyType(dict(sorted(selected.items())))
