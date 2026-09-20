"""Descriptor-aware channel histogram mapping to immutable desired MIDI state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

import cv2
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
    InputPortSpec,
    NodeDefinition,
    NodeExecutionContract,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    PureFunctionRuntime,
    require_same_clock,
)
from synesthesia_machine.nodes.synesthesia.musical import (
    COMMON_MUSICAL_PARAMETER_GROUP,
    common_musical_parameter_specs,
    resolve_common_musical_settings,
    validate_common_musical_parameters,
)

CHANNEL_TO_PITCH_TYPE_ID = "synmachine.synesthesia.channel_to_pitch"
LINEAR_NOMINAL_RANGE = "LINEAR_NOMINAL_RANGE"


def _channel_to_pitch_process(
    node_id: UUID,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    context: FrameContext,
) -> Mapping[str, RuntimeValue]:
    musical = resolve_common_musical_settings(parameters)
    midi = channel_histogram_to_midi_state(
        cast(ChannelFrame, inputs["value"]),
        node_id=node_id,
        context=context,
        selector=musical.selector,
        parameter_a=cast(ChannelFrame | None, inputs.get("parameter_a")),
        parameter_b=cast(ChannelFrame | None, inputs.get("parameter_b")),
        occupancy_threshold_percent=cast(float, parameters["occupancy_threshold_percent"]),
        minimum_a=cast(float, inputs.get("minimum_a", parameters["minimum_a"])),
        maximum_a=(
            cast(float, inputs.get("maximum_a", parameters["maximum_a"]))
            if cast(bool, parameters["maximum_a_enabled"])
            else None
        ),
        minimum_b=cast(float, inputs.get("minimum_b", parameters["minimum_b"])),
        maximum_b=(
            cast(float, inputs.get("maximum_b", parameters["maximum_b"]))
            if cast(bool, parameters["maximum_b_enabled"])
            else None
        ),
        ignore_non_finite=cast(bool, parameters["ignore_non_finite"]),
        midi_channel=musical.midi_channel,
        maximum_polyphony=musical.maximum_polyphony,
        minimum_velocity=musical.minimum_velocity,
        maximum_velocity=musical.maximum_velocity,
    )
    return {"midi": midi}


class ChannelToPitchRuntime(PureFunctionRuntime):
    def __init__(self, node_id: UUID) -> None:
        super().__init__(
            node_id, processor=_channel_to_pitch_process, error_code="invalid_channel_to_pitch"
        )


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

    if parameter_a is None and parameter_b is None:
        all_finite = not value.data.size or bool(cv2.checkRange(value.data, quiet=True)[0])
        if not ignore_non_finite and not all_finite:
            raise ValueError("Connected channels contain non-finite values")
        if all_finite:
            valid_pixel_count = value.data.size
            normalized = np.array(value.data, dtype=np.float32, order="C", copy=True)
        else:
            finite = np.isfinite(value.data)
            valid_pixel_count = int(np.count_nonzero(finite))
            normalized = np.asarray(value.data[finite], dtype=np.float32)
    else:
        # The base channel's finite mask seeds the combined mask, so the
        # parameter channels only contribute their own isfinite passes instead
        # of starting from a fresh full-size ones buffer.
        finite = np.isfinite(value.data)
        if not ignore_non_finite and not bool(np.all(finite)):
            raise ValueError("Connected channels contain non-finite values")
        for channel in (parameter_a, parameter_b):
            if channel is None:
                continue
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
        normalized = np.asarray(value.data[valid], dtype=np.float32)

    if valid_pixel_count == 0:
        return MidiStateFrame({}, context, node_id)

    allowed_notes = selector.allowed_notes
    np.subtract(normalized, np.float32(value.nominal_min), out=normalized)
    np.divide(
        normalized,
        np.float32(value.nominal_max - value.nominal_min),
        out=normalized,
    )
    np.clip(normalized, np.float32(0.0), np.float32(1.0), out=normalized)
    np.multiply(normalized, np.float32(len(allowed_notes)), out=normalized)
    bin_indexes = normalized.astype(np.intp)
    np.minimum(bin_indexes, len(allowed_notes) - 1, out=bin_indexes)
    counts = np.bincount(bin_indexes.ravel(), minlength=len(allowed_notes))
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
            execution=NodeExecutionContract(
                CHANNEL_TO_PITCH_TYPE_ID,
                1,
                ExecutionKind.STATELESS,
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
                        help_text=(
                            "A note only sounds when at least this percentage of the accepted "
                            "pixels "
                            "falls into its bin."
                        ),
                        minimum=0.0,
                        maximum=100.0,
                        editor_hint=ParameterEditorHint.SLIDER,
                    ),
                    ParameterSpec(
                        "minimum_a",
                        "Minimum A",
                        PortType.FLOAT,
                        0.0,
                        help_text="Pixels whose parameter A channel is below this value are "
                        "ignored.",
                        connectable=True,
                        connected_port_type=PortType.FLOAT,
                    ),
                    ParameterSpec(
                        "maximum_a_enabled",
                        "Enable maximum A",
                        PortType.BOOL,
                        False,
                        help_text=(
                            "When on, parameter A pixels above the maximum A value are also "
                            "ignored."
                        ),
                    ),
                    ParameterSpec(
                        "maximum_a",
                        "Maximum A",
                        PortType.FLOAT,
                        1.0,
                        help_text=(
                            "Highest parameter A value counted; only used when Enable maximum A is "
                            "on."
                        ),
                        connectable=True,
                        connected_port_type=PortType.FLOAT,
                    ),
                    ParameterSpec(
                        "minimum_b",
                        "Minimum B",
                        PortType.FLOAT,
                        0.0,
                        help_text="Pixels whose parameter B channel is below this value are "
                        "ignored.",
                        connectable=True,
                        connected_port_type=PortType.FLOAT,
                    ),
                    ParameterSpec(
                        "maximum_b_enabled",
                        "Enable maximum B",
                        PortType.BOOL,
                        False,
                        help_text=(
                            "When on, parameter B pixels above the maximum B value are also "
                            "ignored."
                        ),
                    ),
                    ParameterSpec(
                        "maximum_b",
                        "Maximum B",
                        PortType.FLOAT,
                        1.0,
                        help_text=(
                            "Highest parameter B value counted; only used when Enable maximum B is "
                            "on."
                        ),
                        connectable=True,
                        connected_port_type=PortType.FLOAT,
                    ),
                    ParameterSpec(
                        "ignore_non_finite",
                        "Ignore non-finite values",
                        PortType.BOOL,
                        True,
                        help_text=(
                            "When on, non-finite values (such as NaN or infinity) are skipped "
                            "instead "
                            "of making the node fail."
                        ),
                    ),
                    ParameterSpec(
                        "binning_mode",
                        "Binning mode",
                        PortType.STRING,
                        LINEAR_NOMINAL_RANGE,
                        help_text=(
                            "Chooses how values are spread across the note bins; only linear "
                            "binning "
                            "across the channel's nominal range is available."
                        ),
                        choices=(LINEAR_NOMINAL_RANGE,),
                    ),
                ),
                ChannelToPitchRuntime,
                parameter_validator=_validate_parameters,
            ),
            presentation=NodePresentationIntent(
                "Channel to Pitch",
                "Synesthesia",
                "Turns a channel into notes using a histogram of its values. Values that "
                "appear "
                "often get louder notes; you choose the root, the scale, and the note range. "
                "Optional extra channels can filter which pixels are counted.",
                aliases=("histogram notes", "channel histogram", "image to midi"),
                parameter_groups=(COMMON_MUSICAL_PARAMETER_GROUP,),
            ),
        ),
    )


def _validate_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    errors = list(validate_common_musical_parameters(parameters))
    if cast(bool, parameters["maximum_a_enabled"]) and cast(float, parameters["minimum_a"]) > cast(
        float, parameters["maximum_a"]
    ):
        errors.append("Minimum A cannot exceed maximum A")
    if cast(bool, parameters["maximum_b_enabled"]) and cast(float, parameters["minimum_b"]) > cast(
        float, parameters["maximum_b"]
    ):
        errors.append("Minimum B cannot exceed maximum B")
    return errors


def _validate_channels(channels: tuple[ChannelFrame, ...], context: FrameContext) -> None:
    require_same_clock(
        channels[0].context, context, "Value channel clock does not match the execution clock"
    )
    expected_shape = channels[0].data.shape
    for channel in channels[1:]:
        if channel.data.shape != expected_shape:
            raise ValueError("Connected channels must have identical shapes")
        require_same_clock(
            channels[0].context,
            channel.context,
            "Connected channels must use the same source clock",
        )


def _occupancy_strength(occupancy: float, threshold: float) -> float | None:
    if threshold == 1.0:
        return 1.0 if occupancy == 1.0 else None
    if occupancy <= threshold:
        return None
    return (occupancy - threshold) / (1.0 - threshold)
