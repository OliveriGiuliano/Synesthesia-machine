"""Polymorphic scalar/image/channel utility nodes and array processing."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from threading import Lock
from typing import cast
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
    ImageFrame,
    NodeMemoryDiagnostic,
    ParameterValue,
    PortType,
    RuntimeValue,
    ValueArray,
    read_only_float32,
)
from synesthesia_machine.media.image_common import frame_like
from synesthesia_machine.nodes import (
    ArrayTypeVariable,
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
    TypeVariable,
    VariadicInputSpec,
)
from synesthesia_machine.nodes.migrations import migrate_statistics_v1_to_v2

_DYNAMIC_TYPES = frozenset({PortType.FLOAT, PortType.INT, PortType.IMAGE, PortType.CHANNEL})
_BUFFER_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
T = TypeVariable("T", _DYNAMIC_TYPES)
T_ARRAY = ArrayTypeVariable("T", _DYNAMIC_TYPES)


class _RuntimeBase:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class DifferenceRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        first = cast(ImageFrame, inputs["a"])
        second = cast(ImageFrame, inputs["b"])
        if first.data.shape != second.data.shape:
            raise ExpectedNodeError("difference_shape", "Difference inputs must have equal shapes")
        if (
            first.color_space != second.color_space
            or first.channel_names != second.channel_names
            or first.alpha_mode != second.alpha_mode
        ):
            raise ExpectedNodeError(
                "difference_descriptor", "Difference inputs must use matching image descriptors"
            )
        result = np.abs(first.data - second.data)
        if cast(bool, parameters["normalize"]):
            result = _normalize_array(result, data_minimum=None, data_maximum=None)
        return {"image": frame_like(first, result)}


class ModuloAccumulatorRuntime(_RuntimeBase):
    def __init__(self, node_id: UUID) -> None:
        super().__init__(node_id)
        self._lock = Lock()
        self._accumulator: RuntimeValue | None = None

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        value = inputs["value"]
        modulo = _positive_number(parameters["modulo"], "modulo")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and not float(modulo).is_integer()
        ):
            # The declared output follows the input type (INT here), but
            # wrapping an integer by a fractional modulo yields a float.
            # Surface it as a recoverable usage error rather than a
            # contract violation: the user can switch to a whole-number
            # modulo or a fractional input.
            raise ExpectedNodeError(
                "modulo_not_whole",
                "Modulo must be a whole number when the input value is an integer",
            )
        with self._lock:
            if self._accumulator is None or not _compatible_values(self._accumulator, value):
                self._accumulator = _modulo_value(value, modulo)
            else:
                self._accumulator = _combine_modulo(self._accumulator, value, modulo)
            return {"value": self._accumulator}

    def reset(self, reason: ResetReason) -> None:
        del reason
        with self._lock:
            self._accumulator = None

    def close(self) -> None:
        self.reset(ResetReason.ENGINE_RESTARTED)


class BufferRuntime(_RuntimeBase):
    def __init__(self, node_id: UUID) -> None:
        super().__init__(node_id)
        self._lock = Lock()
        self._values: deque[ImageFrame | ChannelFrame | float | int] = deque()
        self._capacity = 0
        self._signature: tuple[object, ...] | None = None
        self._estimated_retained_bytes = 0

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        value = _array_item(inputs["value"])
        capacity = cast(int, parameters["capacity"])
        item_type = _runtime_port_type(value)
        signature = _buffer_signature(value)
        estimated_retained_bytes = _buffer_item_bytes(value) * capacity
        with self._lock:
            self._capacity = capacity
            self._estimated_retained_bytes = estimated_retained_bytes
            if estimated_retained_bytes > _BUFFER_MEMORY_LIMIT_BYTES:
                self._clear_locked()
                raise ExpectedNodeError(
                    "buffer_memory_limit",
                    "Buffer estimated retained memory exceeds its 256 MiB safety limit",
                    details=(
                        f"estimated_retained_bytes={estimated_retained_bytes}; "
                        f"memory_limit_bytes={_BUFFER_MEMORY_LIMIT_BYTES}"
                    ),
                )
            if capacity != self._values.maxlen or signature != self._signature:
                self._values = deque(maxlen=capacity)
                self._signature = signature
            self._values.append(value)
            return {"values": ValueArray(item_type, tuple(self._values))}

    def reset(self, reason: ResetReason) -> None:
        del reason
        with self._lock:
            self._clear_locked()

    def close(self) -> None:
        self.reset(ResetReason.ENGINE_RESTARTED)

    def node_memory_diagnostic(self) -> NodeMemoryDiagnostic:
        with self._lock:
            return NodeMemoryDiagnostic(
                node_id=self.node_id,
                estimated_retained_bytes=self._estimated_retained_bytes,
                retained_bytes=sum(_buffer_item_bytes(value) for value in self._values),
                retained_frame_count=len(self._values),
                capacity_frame_count=self._capacity,
                memory_limit_bytes=_BUFFER_MEMORY_LIMIT_BYTES,
            )

    def _clear_locked(self) -> None:
        self._values.clear()
        self._signature = None


class StatisticsRuntime(_RuntimeBase):
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
            _require_matching_descriptors(frames)
            data = _statistic(
                np.stack([frame.data for frame in frames]), statistic, percentile, axis=0
            )
            return {"value": frame_like(first, np.asarray(data, dtype=np.float32))}
        if isinstance(first, ChannelFrame):
            channels = tuple(cast(ChannelFrame, value) for value in samples)
            _require_matching_descriptors(channels)
            data = _statistic(
                np.stack([channel.data for channel in channels]), statistic, percentile, axis=0
            )
            return {"value": _channel_like(channels[0], np.asarray(data, dtype=np.float32))}
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


class NormalizeRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        value = inputs["value"]
        mode = cast(str, parameters["mode"])
        output_minimum = cast(float, parameters["output_minimum"])
        output_maximum = cast(float, parameters["output_maximum"])
        if output_maximum < output_minimum:
            raise ExpectedNodeError(
                "normalize_range", "Normalize output maximum must not be below its minimum"
            )
        if isinstance(value, (ImageFrame, ChannelFrame)):
            data = value.data
            input_minimum = cast(float, parameters["input_minimum"]) if mode == "EXPLICIT" else None
            input_maximum = cast(float, parameters["input_maximum"]) if mode == "EXPLICIT" else None
            normalized = _normalize_array(
                data,
                data_minimum=input_minimum,
                data_maximum=input_maximum,
                output_minimum=output_minimum,
                output_maximum=output_maximum,
            )
            return {"value": _value_like(value, normalized)}
        number = cast(float, value)
        input_minimum = cast(float, parameters["input_minimum"])
        input_maximum = cast(float, parameters["input_maximum"])
        if input_maximum == input_minimum:
            raise ExpectedNodeError(
                "normalize_range", "Normalize input endpoints must not be equal"
            )
        result = output_minimum + (number - input_minimum) * (output_maximum - output_minimum) / (
            input_maximum - input_minimum
        )
        return {"value": round(result) if isinstance(value, int) else float(result)}


class CurveRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        value = inputs["value"]
        curve = cast(str, parameters["curve"])
        exponent = _positive_number(parameters["exponent"], "exponent")
        gain = _positive_number(parameters["gain"], "gain")
        midpoint = cast(float, parameters["midpoint"])

        def transform(data: NDArray[np.float32]) -> NDArray[np.float32]:
            source = np.asarray(data, dtype=np.float32)
            with np.errstate(over="ignore", invalid="ignore"):
                if curve == "POWER":
                    return np.asarray(
                        np.sign(source) * np.power(np.abs(source), np.float32(exponent)),
                        dtype=np.float32,
                    )
                if curve == "SIGMOID":
                    return np.asarray(
                        1.0 / (1.0 + np.exp(-gain * (source - midpoint))), dtype=np.float32
                    )
                if curve == "SMOOTHSTEP":
                    clipped = np.clip(source, 0.0, 1.0)
                    return np.asarray(clipped * clipped * (3.0 - 2.0 * clipped), dtype=np.float32)
                return np.asarray(np.expm1(gain * source) / np.expm1(gain), dtype=np.float32)

        if isinstance(value, (ImageFrame, ChannelFrame)):
            return {"value": _value_like(value, transform(value.data))}
        scalar_data = np.asarray([cast(float, value)], dtype=np.float32)
        result = float(transform(scalar_data)[0])
        return {"value": round(result) if isinstance(value, int) else result}


def _validate_normalize_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    output_minimum = cast(float, parameters["output_minimum"])
    output_maximum = cast(float, parameters["output_maximum"])
    if output_maximum < output_minimum:
        return ("output maximum must not be below output minimum",)
    return ()


def _create_all_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            "synmachine.image.difference",
            1,
            "Difference",
            "Image / Compositing",
            "Shows only what is different between two images. Identical areas become black.",
            (
                InputPortSpec("a", "A", PortType.IMAGE),
                InputPortSpec("b", "B", PortType.IMAGE),
            ),
            (OutputPortSpec("image", "Difference", PortType.IMAGE),),
            (
                ParameterSpec(
                    "normalize",
                    "Normalize",
                    PortType.BOOL,
                    False,
                    help_text="When on, the difference is scaled to use the full 0 to 1 range.",
                ),
            ),
            ExecutionKind.STATELESS,
            DifferenceRuntime,
            aliases=("image difference", "absolute difference", "diff"),
        ),
        NodeDefinition(
            "synmachine.utility.modulo_accumulator",
            1,
            "Modulo Accumulator",
            "Utility / Temporal",
            "Adds the input to a running total every frame. When the total passes the limit, it "
            "wraps around, keeping the remainder.",
            (InputPortSpec("value", "Value", T),),
            (OutputPortSpec("value", "Accumulated", T),),
            (
                ParameterSpec(
                    "modulo",
                    "Modulo",
                    PortType.FLOAT,
                    1.0,
                    help_text=(
                        "Value the accumulator wraps around; the output is the accumulated total "
                        "taken modulo this value."
                    ),
                    minimum=1e-12,
                ),
            ),
            ExecutionKind.STATEFUL,
            ModuloAccumulatorRuntime,
            aliases=("wrapped accumulator", "mod accumulator"),
        ),
        NodeDefinition(
            "synmachine.utility.buffer",
            1,
            "Buffer",
            "Utility / Temporal",
            "Remembers the last few values (numbers, images, or channels) and passes them on as a "
            "group each frame.",
            (InputPortSpec("value", "Value", T),),
            (OutputPortSpec("values", "Values", T_ARRAY),),
            (
                ParameterSpec(
                    "capacity",
                    "Capacity",
                    PortType.INT,
                    8,
                    help_text=(
                        "Number of past frames kept in the buffer; the buffer is capped at 256 MiB."
                    ),
                    minimum=1,
                    maximum=600,
                    update_mode=ParameterUpdateMode.RECOMPILE,
                ),
            ),
            ExecutionKind.STATEFUL,
            BufferRuntime,
            aliases=("fifo", "history", "window"),
        ),
        NodeDefinition(
            "synmachine.utility.statistics",
            2,
            "Statistics",
            "Utility / Analysis",
            "Calculates a statistic (mean, median, minimum, maximum, and more) across everything "
            "connected to it, or across a Buffer of values. For numbers it returns one number; "
            "for images and channels it calculates the statistic pixel by pixel.",
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
            ExecutionKind.STATELESS,
            StatisticsRuntime,
            aliases=("array statistics", "mean", "median", "standard deviation"),
            variadic_input=VariadicInputSpec("values", "Value", T_ARRAY, minimum_count=1),
            migrations={1: migrate_statistics_v1_to_v2},
        ),
        NodeDefinition(
            "synmachine.utility.normalize",
            1,
            "Normalize",
            "Utility / Transform",
            "Scales a value into a range you choose (by default 0 to 1).",
            (InputPortSpec("value", "Value", T),),
            (OutputPortSpec("value", "Normalized", T),),
            (
                ParameterSpec(
                    "mode",
                    "Range",
                    PortType.STRING,
                    "DATA_RANGE",
                    help_text=(
                        "Data range reads the smallest and largest values present in the input; "
                        "Explicit uses the input minimum and maximum below."
                    ),
                    choices=("DATA_RANGE", "EXPLICIT"),
                ),
                ParameterSpec(
                    "input_minimum",
                    "Input minimum",
                    PortType.FLOAT,
                    0.0,
                    help_text="Lowest input value when the range is explicit.",
                ),
                ParameterSpec(
                    "input_maximum",
                    "Input maximum",
                    PortType.FLOAT,
                    1.0,
                    help_text="Highest input value when the range is explicit.",
                ),
                ParameterSpec(
                    "output_minimum",
                    "Output minimum",
                    PortType.FLOAT,
                    0.0,
                    help_text="Lowest value the node outputs.",
                ),
                ParameterSpec(
                    "output_maximum",
                    "Output maximum",
                    PortType.FLOAT,
                    1.0,
                    help_text="Highest value the node outputs.",
                ),
            ),
            ExecutionKind.STATELESS,
            NormalizeRuntime,
            aliases=("normalize range", "unit range", "scale values"),
            parameter_validator=_validate_normalize_parameters,
        ),
        NodeDefinition(
            "synmachine.utility.curve",
            1,
            "Curve",
            "Utility / Transform",
            "Bends a value along a curve, so equal changes in the input give bigger or smaller "
            "changes in the output.",
            (InputPortSpec("value", "Value", T),),
            (OutputPortSpec("value", "Curved", T),),
            (
                ParameterSpec(
                    "curve",
                    "Curve",
                    PortType.STRING,
                    "POWER",
                    help_text=(
                        "Chooses the curve shape applied to the value: Power, Sigmoid, "
                        "Smoothstep, or Exponential."
                    ),
                    choices=("POWER", "SIGMOID", "SMOOTHSTEP", "EXPONENTIAL"),
                ),
                ParameterSpec(
                    "exponent",
                    "Exponent",
                    PortType.FLOAT,
                    1.0,
                    help_text=(
                        "Used by the Power curve: raises the absolute value to this power, "
                        "keeping the sign."
                    ),
                    minimum=1e-12,
                ),
                ParameterSpec(
                    "gain",
                    "Gain",
                    PortType.FLOAT,
                    4.0,
                    help_text=(
                        "Steepness of the Sigmoid and Exponential curves; higher values make the "
                        "bend sharper."
                    ),
                    minimum=1e-12,
                ),
                ParameterSpec(
                    "midpoint",
                    "Midpoint",
                    PortType.FLOAT,
                    0.5,
                    help_text="Centre of the Sigmoid curve; the input value that maps to 0.5.",
                ),
            ),
            ExecutionKind.STATELESS,
            CurveRuntime,
            aliases=("response curve", "power curve", "sigmoid", "smoothstep"),
        ),
    )


def create_difference_definitions() -> tuple[NodeDefinition, ...]:
    return _create_all_definitions()[:1]


def create_dynamic_definitions() -> tuple[NodeDefinition, ...]:
    return _create_all_definitions()[1:]


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


def _normalize_array(
    data: NDArray[np.float32],
    *,
    data_minimum: float | None,
    data_maximum: float | None,
    output_minimum: float = 0.0,
    output_maximum: float = 1.0,
) -> NDArray[np.float32]:
    # NaN-safe bounds avoid the full-frame finite gather the old code paid on
    # every call: a couple of C-level reduction passes replace the mask +
    # gather + reduce sequence, and min/max of the same value set are
    # bit-identical. Data containing infinities falls back to the strict
    # finite-only bounds so the documented behavior is preserved.
    has_finite = bool(data.size and np.isfinite(data).any())
    if data_minimum is None:
        if has_finite:
            data_minimum = float(np.nanmin(data))
            if not math.isfinite(data_minimum):
                data_minimum = float(np.min(data[np.isfinite(data)]))
        else:
            data_minimum = 0.0
    if data_maximum is None:
        if has_finite:
            data_maximum = float(np.nanmax(data))
            if not math.isfinite(data_maximum):
                data_maximum = float(np.max(data[np.isfinite(data)]))
        else:
            data_maximum = 0.0
    if data_maximum == data_minimum:
        return np.full_like(data, output_minimum, dtype=np.float32)
    with np.errstate(invalid="ignore", over="ignore"):
        result = output_minimum + (data - data_minimum) * (
            (output_maximum - output_minimum) / (data_maximum - data_minimum)
        )
    return np.asarray(result, dtype=np.float32)


def _modulo_value(value: RuntimeValue, modulo: float) -> RuntimeValue:
    if isinstance(value, (ImageFrame, ChannelFrame)):
        return _value_like(value, np.mod(value.data, modulo))
    number = cast(float, value)
    result = number % modulo
    return int(result) if isinstance(value, int) and float(modulo).is_integer() else float(result)


def _combine_modulo(accumulator: RuntimeValue, value: RuntimeValue, modulo: float) -> RuntimeValue:
    if isinstance(value, (ImageFrame, ChannelFrame)):
        assert isinstance(accumulator, (ImageFrame, ChannelFrame))
        return _value_like(value, np.mod(accumulator.data + value.data, modulo))
    result = (cast(float, accumulator) + cast(float, value)) % modulo
    return int(result) if isinstance(value, int) and float(modulo).is_integer() else float(result)


def _compatible_values(first: RuntimeValue, second: RuntimeValue) -> bool:
    if type(first) is not type(second):
        return False
    if isinstance(first, (ImageFrame, ChannelFrame)):
        assert isinstance(second, (ImageFrame, ChannelFrame))
        return _same_descriptor(first, second)
    return isinstance(first, (int, float)) and not isinstance(first, bool)


def _value_like(value: ImageFrame | ChannelFrame, data: NDArray[np.floating]) -> RuntimeValue:
    converted = np.asarray(data, dtype=np.float32)
    return (
        frame_like(value, converted)
        if isinstance(value, ImageFrame)
        else _channel_like(value, converted)
    )


def _channel_like(channel: ChannelFrame, data: NDArray[np.floating]) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray(data, dtype=np.float32)),
        channel.semantic,
        channel.nominal_min,
        channel.nominal_max,
        channel.cyclic,
        channel.context,
    )


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


def _require_matching_descriptors(values: Sequence[ImageFrame | ChannelFrame]) -> None:
    if any(value.data.shape != values[0].data.shape for value in values[1:]):
        raise ExpectedNodeError(
            "statistics_shape", "Statistics array values must have equal shapes"
        )
    if any(not _same_descriptor(values[0], value) for value in values[1:]):
        raise ExpectedNodeError(
            "statistics_descriptor",
            "Statistics array values must use matching descriptors and source clocks",
        )


def _same_descriptor(
    first: ImageFrame | ChannelFrame,
    second: ImageFrame | ChannelFrame,
) -> bool:
    if type(first) is not type(second) or first.data.shape != second.data.shape:
        return False
    if first.context.clock_id != second.context.clock_id:
        return False
    if isinstance(first, ImageFrame):
        assert isinstance(second, ImageFrame)
        return (
            first.color_space == second.color_space
            and first.channel_names == second.channel_names
            and first.alpha_mode == second.alpha_mode
        )
    assert isinstance(first, ChannelFrame) and isinstance(second, ChannelFrame)
    return (
        first.semantic == second.semantic
        and first.nominal_min == second.nominal_min
        and first.nominal_max == second.nominal_max
        and first.cyclic == second.cyclic
    )


def _runtime_port_type(value: object) -> PortType:
    if isinstance(value, ImageFrame):
        return PortType.IMAGE
    if isinstance(value, ChannelFrame):
        return PortType.CHANNEL
    if isinstance(value, int) and not isinstance(value, bool):
        return PortType.INT
    if isinstance(value, float):
        return PortType.FLOAT
    raise TypeError(f"Unsupported dynamic value {type(value).__name__}")


def _buffer_item_bytes(value: ImageFrame | ChannelFrame | float | int) -> int:
    return value.data.nbytes if isinstance(value, (ImageFrame, ChannelFrame)) else 8


def _buffer_signature(value: ImageFrame | ChannelFrame | float | int) -> tuple[object, ...]:
    if isinstance(value, ImageFrame):
        return (
            PortType.IMAGE,
            value.data.shape,
            value.color_space,
            value.channel_names,
            value.alpha_mode,
            value.context.clock_id,
        )
    if isinstance(value, ChannelFrame):
        return (
            PortType.CHANNEL,
            value.data.shape,
            value.semantic,
            value.nominal_min,
            value.nominal_max,
            value.cyclic,
            value.context.clock_id,
        )
    return (_runtime_port_type(value),)


def _array_item(value: RuntimeValue) -> ImageFrame | ChannelFrame | float | int:
    _runtime_port_type(value)
    return value  # type: ignore[return-value]


def _positive_number(value: object, name: str) -> float:
    result = cast(float, value)
    if not math.isfinite(result) or result <= 0.0:
        raise ExpectedNodeError("invalid_parameter", f"{name} must be finite and positive")
    return result


__all__ = ["create_difference_definitions", "create_dynamic_definitions"]
