"""Built-in scalar utility node definitions and runtimes."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from uuid import UUID

from synesthesia_machine.contracts.runtime_values import (
    FrameContext,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.nodes.base import (
    CachePolicy,
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ResetReason,
    TypeVariable,
)
from synesthesia_machine.nodes.registry import NodeRegistry

T = TypeVariable("T")


class _RuntimeBase:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class NumberRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, context
        if parameters["number_type"] == "INT":
            return {"value": _integer(parameters["int_value"])}
        return {"value": _number(parameters["float_value"])}


class PassThroughRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        return {"value": inputs["value"]}


class ConditionalRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        return {"value": inputs["if_true"] if _boolean(inputs["condition"]) else inputs["if_false"]}


class CompareRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        a, b = _number(inputs["a"]), _number(inputs["b"])
        operation = _text(parameters["operation"])
        if operation == "EQ":
            value = a == b
        elif operation == "NE":
            value = a != b
        elif operation == "LT":
            value = a < b
        elif operation == "LE":
            value = a <= b
        elif operation == "GT":
            value = a > b
        elif operation == "GE":
            value = a >= b
        else:
            value = math.isclose(
                a,
                b,
                rel_tol=_number(parameters["relative_tolerance"]),
                abs_tol=_number(parameters["absolute_tolerance"]),
            )
        return {"value": value}


class LogicRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        a, b = _boolean(inputs["a"]), _boolean(inputs["b"])
        operation = _text(parameters["operation"])
        values = {
            "AND": a and b,
            "OR": a or b,
            "XOR": a != b,
            "NAND": not (a and b),
            "NOR": not (a or b),
            "XNOR": a == b,
        }
        return {"value": values[operation]}


class MathRuntime(_RuntimeBase):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        a = _number(inputs["a"])
        operation = _text(parameters["operation"])
        try:
            if operation == "ABS":
                return {"value": abs(a)}
            if operation == "NEGATE":
                return {"value": -a}
            if operation == "FLOOR":
                return {"value": float(math.floor(a))}
            if operation == "CEIL":
                return {"value": float(math.ceil(a))}
            if operation == "ROUND":
                return {"value": float(round(a))}
            if operation == "SIN":
                return {"value": math.sin(a)}
            if operation == "COS":
                return {"value": math.cos(a)}
            if operation == "TAN":
                return {"value": math.tan(a)}
            b = _number(inputs["b"])
            if operation == "ADD":
                value = a + b
            elif operation == "SUBTRACT":
                value = a - b
            elif operation == "MULTIPLY":
                value = a * b
            elif operation == "DIVIDE":
                value = a / b
            elif operation == "MODULO":
                value = a % b
            elif operation == "POWER":
                value = math.pow(a, b)
            elif operation == "MINIMUM":
                value = min(a, b)
            else:
                value = max(a, b)
            return {"value": value}
        except (ArithmeticError, OverflowError, ValueError) as error:
            raise ExpectedNodeError(
                "math_domain", f"Math operation {operation} failed", details=str(error)
            ) from error


def _number_port_type(
    port_id: str, is_output: bool, parameters: Mapping[str, ParameterValue]
) -> PortType:
    del port_id, is_output
    return PortType.INT if parameters["number_type"] == "INT" else PortType.FLOAT


def _math_required(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    unary = {"ABS", "NEGATE", "FLOOR", "CEIL", "ROUND", "SIN", "COS", "TAN"}
    return ("a",) if parameters["operation"] in unary else ("a", "b")


def create_utility_registry() -> NodeRegistry:
    """Return a registry containing the stable Phase 1 utility catalogue."""
    definitions = (
        NodeDefinition(
            "synmachine.utility.number",
            1,
            "Number",
            "Utility",
            "Static scalar literal.",
            (),
            (OutputPortSpec("value", "Value", PortType.FLOAT),),
            (
                ParameterSpec(
                    "number_type", "Type", PortType.STRING, "FLOAT", choices=("FLOAT", "INT")
                ),
                ParameterSpec("float_value", "Float value", PortType.FLOAT, 0.0),
                ParameterSpec("int_value", "Integer value", PortType.INT, 0),
            ),
            ExecutionKind.STATELESS,
            NumberRuntime,
            cache_policy=CachePolicy.STATIC,
            port_type_resolver=_number_port_type,
            aliases=("constant", "literal", "scalar"),
        ),
        NodeDefinition(
            "synmachine.utility.pass_through",
            1,
            "Pass Through",
            "Utility",
            "Forwards a value without copying.",
            (InputPortSpec("value", "Value", T),),
            (OutputPortSpec("value", "Value", T),),
            (),
            ExecutionKind.STATELESS,
            PassThroughRuntime,
            aliases=("identity", "passthrough", "relay"),
        ),
        NodeDefinition(
            "synmachine.utility.conditional",
            1,
            "Conditional",
            "Utility",
            "Selects one pre-evaluated branch.",
            (
                InputPortSpec("condition", "Condition", PortType.BOOL),
                InputPortSpec("if_true", "If true", T),
                InputPortSpec("if_false", "If false", T),
            ),
            (OutputPortSpec("value", "Value", T),),
            (),
            ExecutionKind.STATELESS,
            ConditionalRuntime,
            aliases=("if", "select", "switch"),
        ),
        NodeDefinition(
            "synmachine.utility.compare",
            1,
            "Compare",
            "Utility",
            "Compares numeric scalars.",
            (InputPortSpec("a", "A", PortType.FLOAT), InputPortSpec("b", "B", PortType.FLOAT)),
            (OutputPortSpec("value", "Value", PortType.BOOL),),
            (
                ParameterSpec(
                    "operation",
                    "Operation",
                    PortType.STRING,
                    "EQ",
                    choices=("EQ", "NE", "LT", "LE", "GT", "GE", "APPROX"),
                ),
                ParameterSpec(
                    "absolute_tolerance", "Absolute tolerance", PortType.FLOAT, 1e-9, minimum=0.0
                ),
                ParameterSpec(
                    "relative_tolerance", "Relative tolerance", PortType.FLOAT, 1e-9, minimum=0.0
                ),
            ),
            ExecutionKind.STATELESS,
            CompareRuntime,
            aliases=("comparison", "equals", "relational"),
        ),
        NodeDefinition(
            "synmachine.utility.logic",
            1,
            "Logic Operation",
            "Utility",
            "Combines booleans.",
            (InputPortSpec("a", "A", PortType.BOOL), InputPortSpec("b", "B", PortType.BOOL)),
            (OutputPortSpec("value", "Value", PortType.BOOL),),
            (
                ParameterSpec(
                    "operation",
                    "Operation",
                    PortType.STRING,
                    "AND",
                    choices=("AND", "OR", "XOR", "NAND", "NOR", "XNOR"),
                ),
            ),
            ExecutionKind.STATELESS,
            LogicRuntime,
            aliases=("boolean", "and", "or", "xor"),
        ),
        NodeDefinition(
            "synmachine.utility.math",
            1,
            "Math",
            "Utility",
            "Performs scalar arithmetic.",
            (
                InputPortSpec("a", "A", PortType.FLOAT),
                InputPortSpec("b", "B", PortType.FLOAT, required=False),
            ),
            (OutputPortSpec("value", "Value", PortType.FLOAT),),
            (
                ParameterSpec(
                    "operation",
                    "Operation",
                    PortType.STRING,
                    "ADD",
                    choices=(
                        "ADD",
                        "SUBTRACT",
                        "MULTIPLY",
                        "DIVIDE",
                        "MODULO",
                        "POWER",
                        "MINIMUM",
                        "MAXIMUM",
                        "ABS",
                        "NEGATE",
                        "FLOOR",
                        "CEIL",
                        "ROUND",
                        "SIN",
                        "COS",
                        "TAN",
                    ),
                ),
            ),
            ExecutionKind.STATELESS,
            MathRuntime,
            required_input_resolver=_math_required,
            aliases=("arithmetic", "calculator", "add", "subtract", "multiply", "divide"),
        ),
    )
    return NodeRegistry(definitions)


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected numeric value, got {type(value).__name__}")


def _integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected integer value, got {type(value).__name__}")


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean value, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected string value, got {type(value).__name__}")
