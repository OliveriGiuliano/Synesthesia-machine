"""Shared Qt-free musical metadata and deterministic MIDI-state construction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.contracts import FrameContext, MidiStateFrame, ParameterValue, PortType
from synesthesia_machine.midi import (
    BUILTIN_SCALE_REGISTRY,
    PITCH_CLASS_NAMES,
    MusicalSelector,
    resolve_musical_selector,
    select_midi_notes,
)
from synesthesia_machine.nodes import ParameterGroupSpec, ParameterSpec

COMMON_MUSICAL_PARAMETER_IDS = (
    "root_pitch_class",
    "scale",
    "custom_scale_mask",
    "midi_minimum",
    "midi_maximum",
    "midi_channel",
    "maximum_polyphony",
    "minimum_velocity",
    "maximum_velocity",
)
COMMON_MUSICAL_PARAMETER_GROUP = ParameterGroupSpec(
    "musical",
    "Musical controls",
    COMMON_MUSICAL_PARAMETER_IDS,
)


@dataclass(frozen=True, slots=True)
class CommonMusicalSettings:
    """Resolved common selector and desired-note limits used by synthesis algorithms."""

    selector: MusicalSelector
    midi_channel: int
    maximum_polyphony: int
    minimum_velocity: int
    maximum_velocity: int


def common_musical_parameter_specs() -> tuple[ParameterSpec, ...]:
    """Build the authoritative common specs in stable persisted order."""

    return (
        ParameterSpec(
            "root_pitch_class",
            "Root note",
            PortType.STRING,
            "C",
            choices=PITCH_CLASS_NAMES,
            connectable=False,
        ),
        ParameterSpec(
            "scale",
            "Scale",
            PortType.STRING,
            "chromatic",
            choices=BUILTIN_SCALE_REGISTRY.ids,
            connectable=False,
        ),
        ParameterSpec(
            "custom_scale_mask",
            "Custom scale mask",
            PortType.STRING,
            "111111111111",
            help_text="Twelve 0/1 values from the selected root pitch class.",
            connectable=False,
        ),
        _live_integer_parameter("midi_minimum", "Minimum MIDI note", 0, 0, 127),
        _live_integer_parameter("midi_maximum", "Maximum MIDI note", 127, 0, 127),
        _live_integer_parameter("midi_channel", "MIDI channel", 1, 1, 16),
        _live_integer_parameter("maximum_polyphony", "Maximum polyphony", 16, 1, 128),
        _live_integer_parameter("minimum_velocity", "Minimum velocity", 1, 1, 127),
        _live_integer_parameter("maximum_velocity", "Maximum velocity", 127, 1, 127),
    )


def _live_integer_parameter(
    parameter_id: str,
    label: str,
    default: int,
    minimum: int,
    maximum: int,
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id,
        label,
        PortType.INT,
        default,
        minimum=minimum,
        maximum=maximum,
        connectable=True,
        connected_port_type=PortType.FLOAT,
    )


def resolve_common_musical_settings(
    parameters: Mapping[str, ParameterValue],
) -> CommonMusicalSettings:
    """Resolve persisted common values, including UI channel 1..16 to runtime 0..15."""

    selector = resolve_musical_selector(
        _text(parameters["root_pitch_class"]),
        _text(parameters["scale"]),
        _integer(parameters["midi_minimum"]),
        _integer(parameters["midi_maximum"]),
        custom_pitch_class_mask=_text(parameters["custom_scale_mask"]),
    )
    minimum_velocity = _integer(parameters["minimum_velocity"])
    maximum_velocity = _integer(parameters["maximum_velocity"])
    if minimum_velocity > maximum_velocity:
        raise ValueError("Minimum velocity cannot exceed maximum velocity")
    return CommonMusicalSettings(
        selector,
        _integer(parameters["midi_channel"]) - 1,
        _integer(parameters["maximum_polyphony"]),
        minimum_velocity,
        maximum_velocity,
    )


def validate_common_musical_parameters(
    parameters: Mapping[str, ParameterValue],
) -> Sequence[str]:
    try:
        resolve_common_musical_settings(parameters)
    except (KeyError, TypeError, ValueError) as error:
        return (str(error),)
    return ()


def midi_state_from_candidates(
    candidates: Iterable[tuple[int, float]],
    *,
    settings: CommonMusicalSettings,
    context: FrameContext,
    source_node_id: UUID,
) -> MidiStateFrame:
    """Apply duplicate/ranking/polyphony/velocity rules and own the resulting state."""

    notes = select_midi_notes(
        candidates,
        channel=settings.midi_channel,
        maximum_polyphony=settings.maximum_polyphony,
        minimum_velocity=settings.minimum_velocity,
        maximum_velocity=settings.maximum_velocity,
    )
    return MidiStateFrame(notes, context, source_node_id)


def _integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected integer value, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string value, got {type(value).__name__}")
