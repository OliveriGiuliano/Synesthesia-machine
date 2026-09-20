"""Transform dynamic-type utility nodes: normalization and the transfer curve."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    ChannelFrame,
    FrameContext,
    ImageFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.media.adjustments import normalize_array
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    NodeExecutionContract,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterSpec,
    StatelessRuntime,
)
from synesthesia_machine.nodes.utility.dynamic import T, positive_number, value_like


class NormalizeRuntime(StatelessRuntime):
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
            normalized = normalize_array(
                data,
                data_minimum=input_minimum,
                data_maximum=input_maximum,
                output_minimum=output_minimum,
                output_maximum=output_maximum,
            )
            return {"value": value_like(value, normalized)}
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


class CurveRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        value = inputs["value"]
        curve = cast(str, parameters["curve"])
        exponent = positive_number(parameters["exponent"], "exponent")
        gain = positive_number(parameters["gain"], "gain")
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
            return {"value": value_like(value, transform(value.data))}
        scalar_data = np.asarray([cast(float, value)], dtype=np.float32)
        result = float(transform(scalar_data)[0])
        return {"value": round(result) if isinstance(value, int) else result}


def _validate_normalize_parameters(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    output_minimum = cast(float, parameters["output_minimum"])
    output_maximum = cast(float, parameters["output_maximum"])
    if output_maximum < output_minimum:
        return ("output maximum must not be below output minimum",)
    return ()


def create_transform_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.normalize",
                1,
                ExecutionKind.STATELESS,
                (InputPortSpec("value", "Value", T),),
                (OutputPortSpec("value", "Normalized", T),),
                (
                    ParameterSpec(
                        "mode",
                        "Range",
                        PortType.STRING,
                        "DATA_RANGE",
                        help_text=(
                            "Data range reads the smallest and largest values present in the "
                            "input; "
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
                NormalizeRuntime,
                parameter_validator=_validate_normalize_parameters,
            ),
            presentation=NodePresentationIntent(
                "Normalize",
                "Utility / Transform",
                "Scales a value into a range you choose (by default 0 to 1).",
                aliases=("normalize range", "unit range", "scale values"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.curve",
                1,
                ExecutionKind.STATELESS,
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
                            "Steepness of the Sigmoid and Exponential curves; higher values make "
                            "the "
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
                CurveRuntime,
            ),
            presentation=NodePresentationIntent(
                "Curve",
                "Utility / Transform",
                "Bends a value along a curve, so equal changes in the input give bigger or smaller "
                "changes in the output.",
                aliases=("response curve", "power curve", "sigmoid", "smoothstep"),
            ),
        ),
    )


__all__ = ["create_transform_definitions"]
