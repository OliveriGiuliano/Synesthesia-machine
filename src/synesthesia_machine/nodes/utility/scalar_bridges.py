"""Channel-to-scalar and scalar conversion utility nodes."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from enum import StrEnum
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.nodes.base import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ResetReason,
)


class IntegerConversionMode(StrEnum):
    ROUND = "ROUND"
    FLOOR = "FLOOR"
    CEIL = "CEIL"
    TRUNCATE = "TRUNCATE"


class _ScalarBridgeRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class RemapNumberRuntime(_ScalarBridgeRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        try:
            value = _remap_number(
                _number(inputs["value"]),
                input_minimum=_connected_number(inputs, parameters, "input_minimum"),
                input_maximum=_connected_number(inputs, parameters, "input_maximum"),
                output_minimum=_connected_number(inputs, parameters, "output_minimum"),
                output_maximum=_connected_number(inputs, parameters, "output_maximum"),
                clamp=_boolean(parameters["clamp"]),
            )
        except (ArithmeticError, TypeError, ValueError) as error:
            raise ExpectedNodeError("invalid_remap_number", str(error)) from error
        return {"value": value}


class FloatToIntegerRuntime(_ScalarBridgeRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        value = _number(inputs["value"])
        if not math.isfinite(value):
            raise ExpectedNodeError(
                "finite_required",
                "Float to Integer requires a finite input value",
            )
        mode = IntegerConversionMode(_text(parameters["mode"]))
        if mode is IntegerConversionMode.ROUND:
            result = round(value)
        elif mode is IntegerConversionMode.FLOOR:
            result = math.floor(value)
        elif mode is IntegerConversionMode.CEIL:
            result = math.ceil(value)
        else:
            result = math.trunc(value)
        return {"value": result}


def _remap_number(
    value: float,
    *,
    input_minimum: float,
    input_maximum: float,
    output_minimum: float,
    output_maximum: float,
    clamp: bool,
) -> float:
    if input_minimum == input_maximum:
        raise ValueError("Remap Number input endpoints must not be equal")
    with np.errstate(all="ignore"):
        normalized = float(
            np.divide(
                np.float64(value) - np.float64(input_minimum),
                np.float64(input_maximum) - np.float64(input_minimum),
            )
        )
        if clamp and not math.isnan(normalized):
            normalized = min(1.0, max(0.0, normalized))
        return float(
            np.float64(output_minimum)
            + np.float64(normalized) * (np.float64(output_maximum) - np.float64(output_minimum))
        )


def _validate_remap(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    if _number(parameters["input_minimum"]) == _number(parameters["input_maximum"]):
        return ("input endpoints must not be equal",)
    return ()


def _connected_float_parameter(parameter_id: str, label: str, default: float) -> ParameterSpec:
    return ParameterSpec(
        parameter_id,
        label,
        PortType.FLOAT,
        default,
        connectable=True,
        connected_port_type=PortType.FLOAT,
    )


def create_scalar_bridge_definitions() -> tuple[NodeDefinition, ...]:
    """Return Batch 6 scalar bridge definitions in persistent catalogue order."""

    return (
        NodeDefinition(
            "synmachine.utility.remap_number",
            1,
            "Remap Number",
            "Utility / Scalar",
            "Map a scalar between explicit input and output ranges.",
            (InputPortSpec("value", "Value", PortType.FLOAT),),
            (OutputPortSpec("value", "Value", PortType.FLOAT),),
            (
                _connected_float_parameter("input_minimum", "Input minimum", 0.0),
                _connected_float_parameter("input_maximum", "Input maximum", 1.0),
                _connected_float_parameter("output_minimum", "Output minimum", 0.0),
                _connected_float_parameter("output_maximum", "Output maximum", 1.0),
                ParameterSpec("clamp", "Clamp", PortType.BOOL, False),
            ),
            ExecutionKind.STATELESS,
            RemapNumberRuntime,
            aliases=("map range", "scale number", "normalize number"),
            parameter_validator=_validate_remap,
        ),
        NodeDefinition(
            "synmachine.utility.float_to_integer",
            1,
            "Float to Integer",
            "Utility / Scalar",
            "Convert a finite floating-point value to an integer using an explicit mode.",
            (InputPortSpec("value", "Value", PortType.FLOAT),),
            (OutputPortSpec("value", "Value", PortType.INT),),
            (
                ParameterSpec(
                    "mode",
                    "Mode",
                    PortType.STRING,
                    IntegerConversionMode.ROUND.value,
                    choices=tuple(mode.value for mode in IntegerConversionMode),
                ),
            ),
            ExecutionKind.STATELESS,
            FloatToIntegerRuntime,
            aliases=("round to integer", "float to int", "integer conversion"),
        ),
    )


def _connected_number(
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, ParameterValue],
    parameter_id: str,
) -> float:
    return _number(inputs.get(parameter_id, parameters[parameter_id]))


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected number, got {type(value).__name__}")


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string, got {type(value).__name__}")


__all__ = [
    "IntegerConversionMode",
    "create_scalar_bridge_definitions",
]
