"""Temporal dynamic-type utility nodes: the modulo accumulator and the frame buffer."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from threading import Lock
from typing import cast
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
    ImageFrame,
    NodeMemoryDiagnostic,
    ParameterValue,
    PortType,
    RuntimeValue,
    ValueArray,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    NodeExecutionContract,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
    StatelessRuntime,
)
from synesthesia_machine.nodes.utility.dynamic import (
    T_ARRAY,
    T,
    positive_number,
    same_descriptor,
    value_like,
)

_BUFFER_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024


class ModuloAccumulatorRuntime(StatelessRuntime):
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
        modulo = positive_number(parameters["modulo"], "modulo")
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


class BufferRuntime(StatelessRuntime):
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


def _modulo_value(value: RuntimeValue, modulo: float) -> RuntimeValue:
    if isinstance(value, (ImageFrame, ChannelFrame)):
        return value_like(value, np.mod(value.data, modulo))
    number = cast(float, value)
    result = number % modulo
    return int(result) if isinstance(value, int) and float(modulo).is_integer() else float(result)


def _combine_modulo(accumulator: RuntimeValue, value: RuntimeValue, modulo: float) -> RuntimeValue:
    if isinstance(value, (ImageFrame, ChannelFrame)):
        assert isinstance(accumulator, (ImageFrame, ChannelFrame))
        return value_like(value, np.mod(accumulator.data + value.data, modulo))
    result = (cast(float, accumulator) + cast(float, value)) % modulo
    return int(result) if isinstance(value, int) and float(modulo).is_integer() else float(result)


def _compatible_values(first: RuntimeValue, second: RuntimeValue) -> bool:
    if type(first) is not type(second):
        return False
    if isinstance(first, (ImageFrame, ChannelFrame)):
        assert isinstance(second, (ImageFrame, ChannelFrame))
        return same_descriptor(first, second)
    return isinstance(first, (int, float)) and not isinstance(first, bool)


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


def create_temporal_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.modulo_accumulator",
                1,
                ExecutionKind.STATEFUL,
                (InputPortSpec("value", "Value", T),),
                (OutputPortSpec("value", "Accumulated", T),),
                (
                    ParameterSpec(
                        "modulo",
                        "Modulo",
                        PortType.FLOAT,
                        1.0,
                        help_text=(
                            "Value the accumulator wraps around; the output is the accumulated "
                            "total "
                            "taken modulo this value."
                        ),
                        minimum=1e-12,
                    ),
                ),
                ModuloAccumulatorRuntime,
            ),
            presentation=NodePresentationIntent(
                "Modulo Accumulator",
                "Utility / Temporal",
                "Adds the input to a running total every frame. When the total passes the limit, "
                "it "
                "wraps around, keeping the remainder.",
                aliases=("wrapped accumulator", "mod accumulator"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.buffer",
                1,
                ExecutionKind.STATEFUL,
                (InputPortSpec("value", "Value", T),),
                (OutputPortSpec("values", "Values", T_ARRAY),),
                (
                    ParameterSpec(
                        "capacity",
                        "Capacity",
                        PortType.INT,
                        8,
                        help_text=(
                            "Number of past frames kept in the buffer; the buffer is capped at 256 "
                            "MiB."
                        ),
                        minimum=1,
                        maximum=600,
                        update_mode=ParameterUpdateMode.RECOMPILE,
                    ),
                ),
                BufferRuntime,
            ),
            presentation=NodePresentationIntent(
                "Buffer",
                "Utility / Temporal",
                "Remembers the last few values (numbers, images, or channels) and passes them on "
                "as a "
                "group each frame.",
                aliases=("fifo", "history", "window"),
            ),
        ),
    )


__all__ = ["create_temporal_definitions"]
