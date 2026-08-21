"""Scale selection and exact Channel-to-Pitch histogram semantics."""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    ChannelFrame,
    ChannelSemantic,
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    read_only_float32,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument, LiteralValue
from synesthesia_machine.midi import (
    BUILTIN_SCALE_REGISTRY,
    CUSTOM_SCALE_ID,
    PITCH_CLASS_NAMES,
    ScaleDefinition,
    ScaleRegistry,
    parse_custom_pitch_class_mask,
    resolve_musical_selector,
    select_midi_notes,
)
from synesthesia_machine.nodes.synesthesia import (
    CHANNEL_TO_PITCH_TYPE_ID,
    channel_histogram_to_midi_state,
    create_synesthesia_definitions,
)

SOURCE_ID = UUID("00000000-0000-0000-0000-000000000321")
NODE_ID = UUID("00000000-0000-0000-0000-000000000322")


def _context(*, clock_id: UUID = SOURCE_ID) -> FrameContext:
    return FrameContext(clock_id, 1, 0, 0.0, 1, None, False)


def _channel(
    values: list[list[float]],
    *,
    nominal_minimum: float = 0.0,
    nominal_maximum: float = 1.0,
    clock_id: UUID = SOURCE_ID,
) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(values, dtype=np.float32)),
        ChannelSemantic.GENERIC,
        nominal_minimum,
        nominal_maximum,
        False,
        _context(clock_id=clock_id),
    )


def _notes(state: MidiStateFrame) -> dict[int, int]:
    return {key.note: velocity for key, velocity in state.notes.items()}


def _map(
    value: ChannelFrame,
    *,
    midi_minimum: int = 60,
    midi_maximum: int = 63,
    scale: str = "chromatic",
    root: str = "C",
    parameter_a: ChannelFrame | None = None,
    parameter_b: ChannelFrame | None = None,
    occupancy_threshold_percent: float = 0.0,
    minimum_a: float = 0.0,
    maximum_a: float | None = None,
    minimum_b: float = 0.0,
    maximum_b: float | None = None,
    ignore_non_finite: bool = True,
    midi_channel: int = 0,
    maximum_polyphony: int = 16,
    minimum_velocity: int = 1,
    maximum_velocity: int = 127,
) -> MidiStateFrame:
    selector = resolve_musical_selector(root, scale, midi_minimum, midi_maximum)
    return channel_histogram_to_midi_state(
        value,
        node_id=NODE_ID,
        context=_context(),
        selector=selector,
        parameter_a=parameter_a,
        parameter_b=parameter_b,
        occupancy_threshold_percent=occupancy_threshold_percent,
        minimum_a=minimum_a,
        maximum_a=maximum_a,
        minimum_b=minimum_b,
        maximum_b=maximum_b,
        ignore_non_finite=ignore_non_finite,
        midi_channel=midi_channel,
        maximum_polyphony=maximum_polyphony,
        minimum_velocity=minimum_velocity,
        maximum_velocity=maximum_velocity,
    )


def test_scale_registry_contains_authoritative_stable_catalogue() -> None:
    assert BUILTIN_SCALE_REGISTRY.ids == (
        "chromatic",
        "major",
        "natural_minor",
        "harmonic_minor",
        "melodic_minor",
        "dorian",
        "phrygian",
        "lydian",
        "mixolydian",
        "locrian",
        "major_pentatonic",
        "minor_pentatonic",
        "whole_tone",
        "diminished",
        CUSTOM_SCALE_ID,
    )
    assert len(PITCH_CLASS_NAMES) == 12
    assert all(len(scale.pitch_class_mask) == 12 for scale in BUILTIN_SCALE_REGISTRY)


def test_scale_registry_and_custom_mask_validation_are_strict() -> None:
    scale = ScaleDefinition("one", "One", (True, *([False] * 11)))
    with pytest.raises(ValueError, match="Duplicate scale ID"):
        ScaleRegistry((scale, scale))
    with pytest.raises(ValueError, match="exactly twelve"):
        parse_custom_pitch_class_mask("101")
    with pytest.raises(ValueError, match="0 or 1"):
        parse_custom_pitch_class_mask("11111111111x")


def test_musical_selector_applies_root_scale_and_inclusive_range() -> None:
    selector = resolve_musical_selector("D", "major", 60, 72)
    assert selector.allowed_notes == (61, 62, 64, 66, 67, 69, 71)

    custom = resolve_musical_selector(
        "C#",
        CUSTOM_SCALE_ID,
        60,
        72,
        custom_pitch_class_mask="100000010000",
    )
    assert custom.allowed_notes == (61, 68)


def test_musical_selector_rejects_empty_or_reversed_ranges() -> None:
    with pytest.raises(ValueError, match="minimum cannot exceed"):
        resolve_musical_selector("C", "major", 72, 60)
    with pytest.raises(ValueError, match="does not allow any notes"):
        resolve_musical_selector(
            "C",
            CUSTOM_SCALE_ID,
            60,
            72,
            custom_pitch_class_mask="000000000000",
        )


def test_histogram_uses_nominal_range_clamping_and_last_endpoint_bin() -> None:
    state = _map(
        _channel([[-1.0, 0.0, 0.24, 0.25, 0.5, 0.75, 1.0, 2.0]]),
        maximum_polyphony=4,
    )
    assert _notes(state) == {60: 48, 61: 17, 62: 17, 63: 48}
    assert state.context == _context()
    assert state.source_node_id == NODE_ID


def test_valid_mask_filters_before_histogram_denominator() -> None:
    value = _channel([[0.1, 0.1, 0.9, 0.9]])
    parameter_a = _channel([[1.0, 1.0, 0.0, np.nan]])
    state = _map(
        value,
        parameter_a=parameter_a,
        minimum_a=0.5,
        midi_minimum=60,
        midi_maximum=61,
        occupancy_threshold_percent=75.0,
    )
    assert _notes(state) == {60: 127}


def test_empty_valid_mask_returns_complete_empty_desired_state() -> None:
    value = _channel([[0.1, 0.9]])
    parameter_a = _channel([[0.0, np.nan]])
    state = _map(value, parameter_a=parameter_a, minimum_a=1.0)
    assert state.notes == {}
    assert state.context == value.context


def test_threshold_strength_remap_and_100_percent_special_case() -> None:
    value = _channel([[0.1, 0.1, 0.1, 0.9]])
    remapped = _map(
        value,
        midi_minimum=60,
        midi_maximum=61,
        occupancy_threshold_percent=50.0,
        minimum_velocity=10,
        maximum_velocity=110,
    )
    assert _notes(remapped) == {60: 60}

    full = _map(
        _channel([[0.1, 0.1]]),
        midi_minimum=60,
        midi_maximum=61,
        occupancy_threshold_percent=100.0,
        minimum_velocity=10,
        maximum_velocity=110,
    )
    assert _notes(full) == {60: 110}
    partial = _map(
        value,
        midi_minimum=60,
        midi_maximum=61,
        occupancy_threshold_percent=100.0,
    )
    assert partial.notes == {}


def test_threshold_equality_is_inactive_below_100_percent() -> None:
    state = _map(
        _channel([[0.1, 0.9]]),
        midi_minimum=60,
        midi_maximum=61,
        occupancy_threshold_percent=50.0,
    )
    assert state.notes == {}


def test_polyphony_selects_strongest_with_lower_pitch_tie_break() -> None:
    state = _map(
        _channel([[0.1, 0.1, 0.3, 0.3, 0.3, 0.6, 0.8, 0.8]]),
        maximum_polyphony=2,
        midi_channel=3,
        minimum_velocity=1,
        maximum_velocity=127,
    )
    assert state.notes == {MidiNoteKey(3, 61): 48, MidiNoteKey(3, 60): 33}


def test_common_note_selection_merges_duplicates_before_polyphony() -> None:
    notes = select_midi_notes(
        ((64, 0.2), (60, 0.5), (64, 0.75), (67, 0.75)),
        channel=0,
        maximum_polyphony=2,
        minimum_velocity=10,
        maximum_velocity=110,
    )
    assert notes == {MidiNoteKey(0, 64): 85, MidiNoteKey(0, 67): 85}


def test_non_finite_values_are_ignored_or_reported_by_policy() -> None:
    value = _channel([[0.1, np.nan, np.inf, 0.9]])
    ignored = _map(value, midi_minimum=60, midi_maximum=61)
    assert _notes(ignored) == {60: 64, 61: 64}
    with pytest.raises(ValueError, match="non-finite"):
        _map(value, midi_minimum=60, midi_maximum=61, ignore_non_finite=False)


def test_channel_shape_and_clock_must_match() -> None:
    value = _channel([[0.1, 0.9]])
    with pytest.raises(ValueError, match="identical shapes"):
        _map(value, parameter_a=_channel([[1.0]]))
    with pytest.raises(ValueError, match="same source clock"):
        _map(
            value,
            parameter_a=_channel(
                [[1.0, 1.0]],
                clock_id=UUID("00000000-0000-0000-0000-000000000323"),
            ),
        )


def test_definition_exposes_exact_ports_and_approved_defaults() -> None:
    definition = create_synesthesia_definitions()[0]
    assert definition.type_id == CHANNEL_TO_PITCH_TYPE_ID
    assert tuple(port.id for port in definition.inputs) == (
        "value",
        "parameter_a",
        "parameter_b",
    )
    assert definition.output("midi") is not None
    parameters, errors = definition.parameter_values({})
    assert errors == []
    assert parameters["root_pitch_class"] == "C"
    assert parameters["scale"] == "chromatic"
    assert parameters["midi_minimum"] == 0
    assert parameters["midi_maximum"] == 127
    assert parameters["midi_channel"] == 1
    assert parameters["occupancy_threshold_percent"] == 0.0
    assert parameters["ignore_non_finite"] is True


@pytest.mark.parametrize(
    "parameters, message",
    [
        ({"midi_minimum": 72, "midi_maximum": 60}, "minimum cannot exceed"),
        ({"minimum_velocity": 100, "maximum_velocity": 99}, "Minimum velocity"),
        (
            {"scale": CUSTOM_SCALE_ID, "custom_scale_mask": "000000000000"},
            "does not allow any notes",
        ),
    ],
)
def test_cross_parameter_errors_fail_graph_compilation(
    parameters: dict[str, LiteralValue], message: str
) -> None:
    document = GraphDocument()
    document.add_node(CHANNEL_TO_PITCH_TYPE_ID, parameters=parameters)
    result = GraphCompiler(create_application_registry()).compile(document.snapshot())
    assert result.plan is None
    assert any(message in issue.message for issue in result.report.errors)
