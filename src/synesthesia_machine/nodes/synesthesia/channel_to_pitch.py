"""Descriptor-aware channel histogram mapping to immutable desired MIDI state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
    MidiStateFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.midi import MusicalSelector, select_midi_notes
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    ResetReason,
)
from synesthesia_machine.nodes.synesthesia.musical import (
    COMMON_MUSICAL_PARAMETER_GROUP,
    common_musical_parameter_specs,
    resolve_common_musical_settings,
    validate_common_musical_parameters,
)

CHANNEL_TO_PITCH_TYPE_ID = "synmachine.synesthesia.channel_to_pitch"
LINEAR_NOMINAL_RANGE = "LINEAR_NOMINAL_RANGE"


class ChannelToPitchRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            musical = resolve_common_musical_settings(parameters)
            midi = channel_histogram_to_midi_state(
                _channel(inputs["value"]),
                node_id=self.node_id,
                context=context,
                selector=musical.selector,
                parameter_a=_optional_channel(inputs.get("parameter_a")),
                parameter_b=_optional_channel(inputs.get("parameter_b")),
                occupancy_threshold_percent=_number(parameters["occupancy_threshold_percent"]),
                minimum_a=_number(inputs.get("minimum_a", parameters["minimum_a"])),
                maximum_a=(
                    _number(inputs.get("maximum_a", parameters["maximum_a"]))
                    if _boolean(parameters["maximum_a_enabled"])
                    else None
                ),
                minimum_b=_number(inputs.get("minimum_b", parameters["minimum_b"])),
                maximum_b=(
                    _number(inputs.get("maximum_b", parameters["maximum_b"]))
                    if _boolean(parameters["maximum_b_enabled"])
                    else None
                ),
                ignore_non_finite=_boolean(parameters["ignore_non_finite"]),
                midi_channel=musical.midi_channel,
                maximum_polyphony=musical.maximum_polyphony,
                minimum_velocity=musical.minimum_velocity,
                maximum_velocity=musical.maximum_velocity,
            )
        except ValueError as error:
            raise ExpectedNodeError("invalid_channel_to_pitch", str(error)) from error
        return {"midi": midi}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def channel_histogram_to_midi_state(
    value: ChannelFrame,
    *,
    node_id: UUID,
    context: FrameContext,
    selector: MusicalSelector,
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
    """Map one filtered channel histogram to a complete desired-note state."""

    if not 0.0 <= occupancy_threshold_percent <= 100.0:
        raise ValueError("Occupancy threshold percent must be in the range 0..100")
    if maximum_a is not None and minimum_a > maximum_a:
        raise ValueError("Minimum A cannot exceed maximum A")
    if maximum_b is not None and minimum_b > maximum_b:
        raise ValueError("Minimum B cannot exceed maximum B")

    channels = tuple(
        channel for channel in (value, parameter_a, parameter_b) if channel is not None
    )
    _validate_channels(channels, context)

    finite = np.ones(value.data.shape, dtype=np.bool_)
    for channel in channels:
        channel_finite = np.isfinite(channel.data)
        if not ignore_non_finite and not bool(np.all(channel_finite)):
            raise ValueError("Connected channels contain non-finite values")
        finite &= channel_finite

    valid = finite
    if parameter_a is not None:
        valid &= parameter_a.data >= minimum_a
        if maximum_a is not None:
            valid &= parameter_a.data <= maximum_a
    if parameter_b is not None:
        valid &= parameter_b.data >= minimum_b
        if maximum_b is not None:
            valid &= parameter_b.data <= maximum_b

    valid_pixel_count = int(np.count_nonzero(valid))
    if valid_pixel_count == 0:
        return MidiStateFrame({}, context, node_id)

    allowed_notes = selector.allowed_notes
    normalized = (value.data[valid] - value.nominal_min) / (value.nominal_max - value.nominal_min)
    normalized = np.clip(normalized, 0.0, 1.0)
    bin_indexes = np.floor(normalized * len(allowed_notes)).astype(np.intp)
    np.minimum(bin_indexes, len(allowed_notes) - 1, out=bin_indexes)
    counts = np.bincount(bin_indexes, minlength=len(allowed_notes))
    occupancies = counts.astype(np.float64) / valid_pixel_count

    threshold = occupancy_threshold_percent / 100.0
    candidates: list[tuple[int, float]] = []
    for note, occupancy in zip(allowed_notes, occupancies, strict=True):
        strength = _occupancy_strength(float(occupancy), threshold)
        if strength is not None:
            candidates.append((note, strength))

    notes = select_midi_notes(
        candidates,
        channel=midi_channel,
        maximum_polyphony=maximum_polyphony,
        minimum_velocity=minimum_velocity,
        maximum_velocity=maximum_velocity,
    )
    return MidiStateFrame(notes, context, node_id)


def create_synesthesia_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            CHANNEL_TO_PITCH_TYPE_ID,
            1,
            "Channel to Pitch",
            "Synesthesia",
            (
                "Build a histogram from all valid pixels in the Value channel, dividing its "
                "nominal range across the notes allowed by the selected root, scale, and MIDI "
                "range. Optional Parameter A/B channels filter pixels before binning. Bins above "
                "the occupancy threshold become notes, occupancy controls velocity, and the "
                "strongest notes survive the polyphony limit."
            ),
            (
                InputPortSpec("value", "Value", PortType.CHANNEL),
                InputPortSpec("parameter_a", "Parameter A", PortType.CHANNEL, required=False),
                InputPortSpec("parameter_b", "Parameter B", PortType.CHANNEL, required=False),
            ),
            (OutputPortSpec("midi", "MIDI state", PortType.MIDI_STATE),),
            (
                *common_musical_parameter_specs(),
                ParameterSpec(
                    "occupancy_threshold_percent",
                    "Occupancy threshold (%)",
                    PortType.FLOAT,
                    0.0,
                    minimum=0.0,
                    maximum=100.0,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "minimum_a",
                    "Minimum A",
                    PortType.FLOAT,
                    0.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                ),
                ParameterSpec("maximum_a_enabled", "Enable maximum A", PortType.BOOL, False),
                ParameterSpec(
                    "maximum_a",
                    "Maximum A",
                    PortType.FLOAT,
                    1.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                ),
                ParameterSpec(
                    "minimum_b",
                    "Minimum B",
                    PortType.FLOAT,
                    0.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                ),
                ParameterSpec("maximum_b_enabled", "Enable maximum B", PortType.BOOL, False),
                ParameterSpec(
                    "maximum_b",
                    "Maximum B",
                    PortType.FLOAT,
                    1.0,
                    connectable=True,
                    connected_port_type=PortType.FLOAT,
                ),
                ParameterSpec("ignore_non_finite", "Ignore non-finite values", PortType.BOOL, True),
                ParameterSpec(
                    "binning_mode",
                    "Binning mode",
                    PortType.STRING,
                    LINEAR_NOMINAL_RANGE,
                    choices=(LINEAR_NOMINAL_RANGE,),
                ),
            ),
            ExecutionKind.STATELESS,
            ChannelToPitchRuntime,
            aliases=("histogram notes", "channel histogram", "image to midi"),
            parameter_validator=_validate_parameters,
            parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
        ),
    )


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    if _boolean(parameters["maximum_a_enabled"]) and _number(parameters["minimum_a"]) > _number(
        parameters["maximum_a"]
    ):
        errors.append("Minimum A cannot exceed maximum A")
    if _boolean(parameters["maximum_b_enabled"]) and _number(parameters["minimum_b"]) > _number(
        parameters["maximum_b"]
    ):
        errors.append("Minimum B cannot exceed maximum B")
    return errors


def _validate_channels(channels: tuple[ChannelFrame, ...], context: FrameContext) -> None:
    expected_shape = channels[0].data.shape
    expected_clock = channels[0].context.clock_id
    if expected_clock != context.clock_id:
        raise ValueError("Value channel clock does not match the execution clock")
    for channel in channels[1:]:
        if channel.data.shape != expected_shape:
            raise ValueError("Connected channels must have identical shapes")
        if channel.context.clock_id != expected_clock:
            raise ValueError("Connected channels must use the same source clock")


def _occupancy_strength(occupancy: float, threshold: float) -> float | None:
    if threshold == 1.0:
        return 1.0 if occupancy == 1.0 else None
    if occupancy <= threshold:
        return None
    return (occupancy - threshold) / (1.0 - threshold)


def _channel(value: object) -> ChannelFrame:
    if isinstance(value, ChannelFrame):
        return value
    raise TypeError(f"Expected ChannelFrame, got {type(value).__name__}")


def _optional_channel(value: object | None) -> ChannelFrame | None:
    return None if value is None else _channel(value)


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected numeric value, got {type(value).__name__}")


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean value, got {type(value).__name__}")
