"""Built-in scalar utility node definitions and runtimes."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

from synesthesia_machine.contracts import JsonObject
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
    NodeExecutionContract,
    NodePersistenceDescriptor,
    NodePresentationIntent,
    OutputPortSpec,
    ParameterSpec,
    StatelessRuntime,
    TypeVariable,
)
from synesthesia_machine.nodes.migrations import migration_parameters
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.nodes.utility.analysis import create_analysis_definitions
from synesthesia_machine.nodes.utility.midi import create_midi_utility_definitions
from synesthesia_machine.nodes.utility.scalar_bridges import create_scalar_bridge_definitions
from synesthesia_machine.nodes.utility.temporal import create_temporal_definitions
from synesthesia_machine.nodes.utility.transform import create_transform_definitions

T = TypeVariable("T")


def migrate_number_v0_to_v1(data: JsonObject) -> JsonObject:
    migrated = deepcopy(data)
    parameters = migration_parameters(migrated)
    legacy_value = parameters.pop("value", None)
    if isinstance(legacy_value, int) and not isinstance(legacy_value, bool):
        parameters.setdefault("number_type", "INT")
        parameters.setdefault("int_value", legacy_value)
    elif isinstance(legacy_value, float):
        parameters.setdefault("number_type", "FLOAT")
        parameters.setdefault("float_value", legacy_value)
    elif "int_value" in parameters:
        parameters.setdefault("number_type", "INT")
    else:
        parameters.setdefault("number_type", "FLOAT")
    migrated["implementation_version"] = 1
    return migrated


class NumberRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, context
        if parameters["number_type"] == "INT":
            return {"value": cast(int, parameters["int_value"])}
        return {"value": cast(float, parameters["float_value"])}


class PassThroughRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        return {"value": inputs["value"]}


class ConditionalRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        value = inputs["if_true"] if cast(bool, inputs["condition"]) else inputs["if_false"]
        return {"value": value}


class CompareRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        a, b = cast(float, inputs["a"]), cast(float, inputs["b"])
        operation = cast(str, parameters["operation"])
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
                rel_tol=cast(float, parameters["relative_tolerance"]),
                abs_tol=cast(float, parameters["absolute_tolerance"]),
            )
        return {"value": value}


class LogicRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        a, b = cast(bool, inputs["a"]), cast(bool, inputs["b"])
        operation = cast(str, parameters["operation"])
        values = {
            "AND": a and b,
            "OR": a or b,
            "XOR": a != b,
            "NAND": not (a and b),
            "NOR": not (a or b),
            "XNOR": a == b,
        }
        return {"value": values[operation]}


class MathRuntime(StatelessRuntime):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        a = cast(float, inputs["a"])
        operation = cast(str, parameters["operation"])
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
            b = cast(float, inputs["b"])
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
    """Return a registry containing the stable scalar utility catalogue."""
    definitions = (
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.number",
                1,
                ExecutionKind.STATELESS,
                (),
                (OutputPortSpec("value", "Value", PortType.FLOAT),),
                (
                    ParameterSpec(
                        "number_type",
                        "Type",
                        PortType.STRING,
                        "FLOAT",
                        help_text=(
                            "Chooses the type of number this node outputs: Float or Integer."
                        ),
                        choices=("FLOAT", "INT"),
                    ),
                    ParameterSpec(
                        "float_value",
                        "Float value",
                        PortType.FLOAT,
                        0.0,
                        help_text="The float value this node outputs; used when the type is Float.",
                    ),
                    ParameterSpec(
                        "int_value",
                        "Integer value",
                        PortType.INT,
                        0,
                        help_text="The integer value this node outputs; used when the type is "
                        "Integer.",
                    ),
                ),
                NumberRuntime,
                cache_policy=CachePolicy.STATIC,
                port_type_resolver=_number_port_type,
            ),
            presentation=NodePresentationIntent(
                "Number",
                "Utility",
                "A fixed number that never changes.",
                aliases=("constant", "literal", "scalar"),
            ),
            persistence=NodePersistenceDescriptor(
                migrations={0: migrate_number_v0_to_v1},
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.pass_through",
                1,
                ExecutionKind.STATELESS,
                (InputPortSpec("value", "Value", T),),
                (OutputPortSpec("value", "Value", T),),
                (),
                PassThroughRuntime,
            ),
            presentation=NodePresentationIntent(
                "Pass Through",
                "Utility",
                "Passes the value through to the next node, unchanged.",
                aliases=("identity", "passthrough", "relay"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.conditional",
                1,
                ExecutionKind.STATELESS,
                (
                    InputPortSpec("condition", "Condition", PortType.BOOL),
                    InputPortSpec("if_true", "If true", T),
                    InputPortSpec("if_false", "If false", T),
                ),
                (OutputPortSpec("value", "Value", T),),
                (),
                ConditionalRuntime,
            ),
            presentation=NodePresentationIntent(
                "Conditional",
                "Utility",
                "Picks one of several values based on a condition, like an if/else.",
                aliases=("if", "select", "switch"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.compare",
                1,
                ExecutionKind.STATELESS,
                (InputPortSpec("a", "A", PortType.FLOAT), InputPortSpec("b", "B", PortType.FLOAT)),
                (OutputPortSpec("value", "Value", PortType.BOOL),),
                (
                    ParameterSpec(
                        "operation",
                        "Operation",
                        PortType.STRING,
                        "EQ",
                        help_text="Chooses the comparison applied to the two connected values.",
                        choices=("EQ", "NE", "LT", "LE", "GT", "GE", "APPROX"),
                    ),
                    ParameterSpec(
                        "absolute_tolerance",
                        "Absolute tolerance",
                        PortType.FLOAT,
                        1e-9,
                        help_text=(
                            "Tolerance used by the Approx comparison: two values match if they "
                            "differ "
                            "by no more than this amount."
                        ),
                        minimum=0.0,
                    ),
                    ParameterSpec(
                        "relative_tolerance",
                        "Relative tolerance",
                        PortType.FLOAT,
                        1e-9,
                        help_text=(
                            "Tolerance used by the Approx comparison: two values match if they "
                            "differ "
                            "by no more than this fraction of the other value."
                        ),
                        minimum=0.0,
                    ),
                ),
                CompareRuntime,
            ),
            presentation=NodePresentationIntent(
                "Compare",
                "Utility",
                "Compares two numbers: is one bigger, smaller, equal to, or close to the other?",
                aliases=("comparison", "equals", "relational"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.logic",
                1,
                ExecutionKind.STATELESS,
                (InputPortSpec("a", "A", PortType.BOOL), InputPortSpec("b", "B", PortType.BOOL)),
                (OutputPortSpec("value", "Value", PortType.BOOL),),
                (
                    ParameterSpec(
                        "operation",
                        "Operation",
                        PortType.STRING,
                        "AND",
                        help_text=(
                            "Chooses the logic operation applied to the connected values, treating "
                            "nonzero as true."
                        ),
                        choices=("AND", "OR", "XOR", "NAND", "NOR", "XNOR"),
                    ),
                ),
                LogicRuntime,
            ),
            presentation=NodePresentationIntent(
                "Logic Operation",
                "Utility",
                "Combines true/false values with AND, OR, XOR, NAND, NOR, or XNOR.",
                aliases=("boolean", "and", "or", "xor"),
            ),
        ),
        NodeDefinition(
            execution=NodeExecutionContract(
                "synmachine.utility.math",
                1,
                ExecutionKind.STATELESS,
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
                        help_text="Chooses the calculation applied to the connected values.",
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
                MathRuntime,
                required_input_resolver=_math_required,
            ),
            presentation=NodePresentationIntent(
                "Math",
                "Utility",
                "Does arithmetic on numbers: add, subtract, multiply, divide, and many other "
                "operations, from powers to rounding and trigonometry.",
                aliases=("arithmetic", "calculator", "add", "subtract", "multiply", "divide"),
            ),
        ),
        *create_midi_utility_definitions(),
        *create_scalar_bridge_definitions(),
        *create_temporal_definitions(),
        *create_analysis_definitions(),
        *create_transform_definitions(),
    )
    return NodeRegistry(definitions)
