"""Analysis dynamic-type utility node: statistics over the connected values."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import cast

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
    ImageFrame,
    JsonObject,
    ParameterValue,
    PortType,
    RuntimeValue,
    ValueArray,
)
from synesthesia_machine.media.image_common import frame_like
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    NodeDefinition,
    NodeExecutionContract,
    NodePersistenceDescriptor,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterSpec,
    StatelessRuntime,
    VariadicInputSpec,
)
from synesthesia_machine.nodes.utility.dynamic import (
    T_ARRAY,
    T,
    channel_like,
    require_matching_descriptors,
)


def migrate_statistics_v1_to_v2(data: JsonObject) -> JsonObject:
    """Statistics v2 replaced its single ``values`` input with variadic sockets.

    No parameter payload changed; the input-port rename lives in the v2 -> v3
    graph migration, so this step only advances the implementation version.
    """

    migrated = deepcopy(data)
    migrated["implementation_version"] = 2
    return migrated


class StatisticsRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        samples = _flatten_samples(inputs.values())
        if not samples:
            raise ExpectedNodeError("statistics_empty", "Statistics requires a non-empty input")
        statistic = cast(str, parameters["statistic"])
        percentile = cast(float, parameters["percentile"])
        first = samples[0]
        if isinstance(first, ImageFrame):
            frames = tuple(cast(ImageFrame, value) for value in samples)
            require_matching_descriptors(frames)
            data = _statistic(
                np.stack([frame.data for frame in frames]), statistic, percentile, axis=0
            )
            return {"value": frame_like(first, np.asarray(data, dtype=np.float32))}
        if isinstance(first, ChannelFrame):
            channels = tuple(cast(ChannelFrame, value) for value in samples)
            require_matching_descriptors(channels)
            data = _statistic(
                np.stack([channel.data for channel in channels]), statistic, percentile, axis=0
            )
            return {"value": channel_like(channels[0], np.asarray(data, dtype=np.float32))}
        if isinstance(first, (int, float)) and not isinstance(first, bool):
            # The compiler unifies the scalar element types, so every sample
            # here is a number; the cast only narrows the static type.
            sample_array = np.asarray([cast(float, value) for value in samples], dtype=np.float64)
            result = _statistic(sample_array, statistic, percentile, axis=0)
            scalar: float | int = float(result)
            all_int = all(
                isinstance(value, int) and not isinstance(value, bool) for value in samples
            )
            # The compiler unifies the element types of every variadic
            # input ({INT, FLOAT} -> FLOAT), so only an all-int set can
            # declare an INT output. MINIMUM and MAXIMUM preserve that type
            # losslessly; every other statistic can turn integers into a
            # fraction (the mean of 1 and 2 is 1.5), so on an INT-declared
            # port those statistics are exact only while the result is a
            # whole number. Anything else must fail as a stable, user-facing
            # error rather than emit an invalid float. A downstream concrete
            # FLOAT consumer can still force a FLOAT declaration for an
            # all-int set; the scheduler applies the implicit INT -> FLOAT
            # conversion at the output boundary, so the emitted int is
            # widened there.
            if all_int:
                if statistic not in {"MINIMUM", "MAXIMUM"} and (
                    not math.isfinite(scalar) or not scalar.is_integer()
                ):
                    raise ExpectedNodeError(
                        "statistics_non_integer",
                        "This statistic of integer values is not a whole number; use "
                        "MINIMUM or MAXIMUM to keep integer results, or provide FLOAT values",
                    )
                scalar = int(scalar)
            return {"value": scalar}
        raise ExpectedNodeError(
            "statistics_type", "Statistics inputs must be scalar, image, or channel values"
        )


def _statistic(
    samples: NDArray[np.floating], statistic: str, percentile: float, *, axis: int
) -> NDArray[np.floating] | np.floating:
    with np.errstate(all="ignore"):
        operations: dict[str, Callable[[], NDArray[np.floating] | np.floating]] = {
            "MEAN": lambda: np.mean(samples, axis=axis),
            "MEDIAN": lambda: np.median(samples, axis=axis),
            "MINIMUM": lambda: np.min(samples, axis=axis),
            "MAXIMUM": lambda: np.max(samples, axis=axis),
            "STANDARD_DEVIATION": lambda: np.std(samples, axis=axis),
            "PERCENTILE": lambda: np.percentile(samples, percentile, axis=axis),
        }
        return operations[statistic]()


def _flatten_samples(values: Iterable[RuntimeValue]) -> list[RuntimeValue]:
    """Flatten each connected value into samples.

    A direct connection delivers a single scalar/image/channel value; a Buffer
    connection delivers a ``ValueArray`` whose elements are the samples. Both
    forms may share one statistics node, so every value is normalized to the
    flat samples the statistic is computed over.
    """

    samples: list[RuntimeValue] = []
    for value in values:
        if isinstance(value, ValueArray):
            samples.extend(value.values)
        else:
            samples.append(value)
    return samples


def create_analysis_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.statistics",
                2,
                ExecutionKind.STATELESS,
                (),
                (OutputPortSpec("value", "Value", T),),
                (
                    ParameterSpec(
                        "statistic",
                        "Statistic",
                        PortType.STRING,
                        "MEAN",
                        help_text=(
                            "Chooses the value that summarizes the samples: Mean, Median, Minimum, "
                            "Maximum, Standard deviation, or Percentile."
                        ),
                        choices=(
                            "MEAN",
                            "MEDIAN",
                            "MINIMUM",
                            "MAXIMUM",
                            "STANDARD_DEVIATION",
                            "PERCENTILE",
                        ),
                    ),
                    ParameterSpec(
                        "percentile",
                        "Percentile",
                        PortType.FLOAT,
                        50.0,
                        help_text=(
                            "Percentile reported when the statistic is Percentile, from 0 to 100."
                        ),
                        minimum=0.0,
                        maximum=100.0,
                    ),
                ),
                StatisticsRuntime,
                variadic_input=VariadicInputSpec("values", "Value", T_ARRAY, minimum_count=1),
            ),
            presentation=NodePresentationIntent(
                "Statistics",
                "Utility / Analysis",
                "Calculates a statistic (mean, median, minimum, maximum, and more) across "
                "everything "
                "connected to it, or across a Buffer of values. For numbers it returns one number; "
                "for images and channels it calculates the statistic pixel by pixel.",
                aliases=("array statistics", "mean", "median", "standard deviation"),
            ),
            persistence=NodePersistenceDescriptor(
                migrations={1: migrate_statistics_v1_to_v2},
            ),
        ),
    )


__all__ = [
    "create_analysis_definitions",
    "migrate_statistics_v1_to_v2",
]
