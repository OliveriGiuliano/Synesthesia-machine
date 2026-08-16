"""Stable metadata and execution contracts for headless node implementations."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    ResetReason,
)
from synesthesia_machine.contracts.engine_client import (
    NodeExecutionError as NodeExecutionError,
)
from synesthesia_machine.contracts.runtime_values import (
    ColorValue,
    FrameContext,
    NumericMatrix,
    ParameterValue,
    PortType,
    RuntimeValue,
)

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_TYPE_ID = re.compile(r"^[a-z][a-z0-9_.]*$")


@dataclass(frozen=True, slots=True)
class TypeVariable:
    name: str
    allowed_types: frozenset[PortType] = frozenset()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.name):
            msg = f"Invalid type-variable name: {self.name!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ArrayTypeVariable:
    """Array counterpart of a node-local type variable (T[])."""

    name: str
    allowed_types: frozenset[PortType] = frozenset()

    def __post_init__(self) -> None:
        TypeVariable(self.name, self.allowed_types)


type PortTypeExpression = PortType | TypeVariable | ArrayTypeVariable


@dataclass(frozen=True, slots=True)
class InputPortSpec:
    id: str
    label: str
    value_type: PortTypeExpression
    required: bool = True

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "input port")


@dataclass(frozen=True, slots=True)
class VariadicInputSpec:
    """Metadata for an unbounded family of one-connection indexed input sockets."""

    id_prefix: str
    label: str
    value_type: PortTypeExpression
    minimum_count: int = 1

    def __post_init__(self) -> None:
        _validate_stable_id(self.id_prefix, "variadic input prefix")
        if self.minimum_count < 1:
            raise ValueError("Variadic input minimum_count must be at least 1")

    def index(self, port_id: str) -> int | None:
        match = re.fullmatch(rf"{re.escape(self.id_prefix)}_([1-9][0-9]*)", port_id)
        return None if match is None else int(match.group(1))

    def port(self, index: int) -> InputPortSpec:
        if index < 1:
            raise ValueError("Variadic input index must be at least 1")
        return InputPortSpec(
            f"{self.id_prefix}_{index}",
            f"{self.label} {index}",
            self.value_type,
            required=index <= self.minimum_count,
        )


@dataclass(frozen=True, slots=True)
class OutputPortSpec:
    id: str
    label: str
    value_type: PortTypeExpression

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "output port")


class ParameterUpdateMode(StrEnum):
    LIVE = "LIVE"
    RECOMPILE = "RECOMPILE"
    RESTART_SOURCE = "RESTART_SOURCE"


class ParameterEditorHint(StrEnum):
    """Optional UI intent kept separate from validation bounds."""

    DEFAULT = "DEFAULT"
    SLIDER = "SLIDER"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    id: str
    label: str
    value_type: PortType
    default: ParameterValue
    help_text: str = ""
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[ParameterValue, ...] = ()
    connectable: bool | None = None
    connected_port_type: PortType | None = None
    update_mode: ParameterUpdateMode = ParameterUpdateMode.LIVE
    editor_hint: ParameterEditorHint = ParameterEditorHint.DEFAULT
    applicable_input_types: tuple[PortType, ...] = ()

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "parameter")
        error = self.validate(self.default)
        if error is not None:
            msg = f"Invalid default for parameter {self.id!r}: {error}"
            raise ValueError(msg)
        if self.connectable is True and self.connected_port_type is None:
            if self.value_type in {PortType.FLOAT, PortType.INT}:
                object.__setattr__(self, "connected_port_type", PortType.FLOAT)
            elif self.value_type is PortType.BOOL:
                object.__setattr__(self, "connected_port_type", PortType.BOOL)
            else:
                msg = f"Connectable parameter {self.id!r} requires connected_port_type"
                raise ValueError(msg)

    def validate(self, value: object) -> str | None:
        if not _matches_port_type(value, self.value_type):
            return f"expected {self.value_type.value}, got {type(value).__name__}"
        if self.choices and value not in self.choices:
            return f"expected one of {self.choices!r}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and not math.isfinite(value):
                return "must be finite"
            if self.minimum is not None and value < self.minimum:
                return f"must be at least {self.minimum}"
            if self.maximum is not None and value > self.maximum:
                return f"must be at most {self.maximum}"
        return None

    def connected_value(self, value: object) -> float | int | bool:
        """Coerce and bound a live socket value to this parameter's literal contract."""

        converted: float | int | bool
        if self.value_type is PortType.FLOAT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"expected numeric scalar, got {type(value).__name__}")
            converted = float(value)
            if not math.isfinite(converted):
                raise ValueError("expected a finite numeric scalar")
        elif self.value_type is PortType.INT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"expected numeric scalar, got {type(value).__name__}")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("expected a finite numeric scalar")
            converted = round(numeric)
        elif self.value_type is PortType.BOOL:
            if not isinstance(value, bool):
                raise ValueError(f"expected BOOL, got {type(value).__name__}")
            converted = value
        else:
            raise ValueError(f"{self.value_type.value} parameters do not accept live scalar input")

        if not isinstance(converted, bool):
            if self.minimum is not None and converted < self.minimum:
                converted = self.minimum
            if self.maximum is not None and converted > self.maximum:
                converted = self.maximum
            converted = int(converted) if self.value_type is PortType.INT else float(converted)
            numeric_choices = tuple(
                choice
                for choice in self.choices
                if isinstance(choice, (int, float)) and not isinstance(choice, bool)
            )
            if numeric_choices:
                converted = min(numeric_choices, key=lambda choice: abs(float(choice) - converted))
        error = self.validate(converted)
        if error is not None:
            raise ValueError(error)
        return converted


@dataclass(frozen=True, slots=True)
class ParameterGroupSpec:
    """Headless metadata for presenting related parameters as one reusable control."""

    id: str
    label: str
    parameter_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "parameter group")
        if not self.parameter_ids:
            raise ValueError("Parameter group must contain at least one parameter")
        for parameter_id in self.parameter_ids:
            _validate_stable_id(parameter_id, "grouped parameter")
        _ensure_unique(self.parameter_ids, "grouped parameter")


class ExecutionKind(StrEnum):
    SOURCE = "SOURCE"
    STATELESS = "STATELESS"
    STATEFUL = "STATEFUL"
    SINK = "SINK"
    VISUALIZER = "VISUALIZER"


class CachePolicy(StrEnum):
    AUTO = "AUTO"
    NEVER = "NEVER"
    STATIC = "STATIC"


class NodeRuntime(Protocol):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]: ...

    def reset(self, reason: ResetReason) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class PanicCapableRuntime(Protocol):
    def panic(self) -> None: ...


@runtime_checkable
class MidiOutputStatusProvider(Protocol):
    def midi_output_status(self) -> MidiOutputStatus: ...


@runtime_checkable
class NodeMemoryDiagnosticProvider(Protocol):
    def node_memory_diagnostic(self) -> NodeMemoryDiagnostic: ...


type RuntimeFactory = Callable[[UUID], NodeRuntime]
type PortTypeResolver = Callable[[str, bool, Mapping[str, ParameterValue]], PortTypeExpression]
type RequiredInputResolver = Callable[[Mapping[str, ParameterValue]], Sequence[str]]
type ParameterValidator = Callable[[Mapping[str, ParameterValue]], Sequence[str]]


@dataclass(frozen=True, slots=True)
class NodeDefinition:
    type_id: str
    implementation_version: int
    display_name: str
    category: str
    description: str
    inputs: tuple[InputPortSpec, ...]
    outputs: tuple[OutputPortSpec, ...]
    parameters: tuple[ParameterSpec, ...]
    execution_kind: ExecutionKind
    runtime_factory: RuntimeFactory
    cache_policy: CachePolicy = CachePolicy.AUTO
    realtime_safe: bool = True
    handles_no_data: bool = False
    port_type_resolver: PortTypeResolver | None = None
    required_input_resolver: RequiredInputResolver | None = None
    aliases: tuple[str, ...] = ()
    parameter_validator: ParameterValidator | None = None
    variadic_input: VariadicInputSpec | None = None
    parameter_groups: tuple[ParameterGroupSpec, ...] = ()

    def __post_init__(self) -> None:
        if not _TYPE_ID.fullmatch(self.type_id):
            msg = f"Invalid node type ID: {self.type_id!r}"
            raise ValueError(msg)
        if self.implementation_version < 1:
            msg = "implementation_version must be at least 1"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "parameters",
            tuple(
                _resolve_parameter_socket(parameter, self.execution_kind, self.cache_policy)
                for parameter in self.parameters
            ),
        )
        _ensure_unique((port.id for port in self.inputs), "input port")
        _ensure_unique((port.id for port in self.outputs), "output port")
        _ensure_unique((parameter.id for parameter in self.parameters), "parameter")
        _ensure_unique((group.id for group in self.parameter_groups), "parameter group")
        parameter_ids = {parameter.id for parameter in self.parameters}
        grouped_parameter_ids = tuple(
            parameter_id for group in self.parameter_groups for parameter_id in group.parameter_ids
        )
        _ensure_unique(grouped_parameter_ids, "grouped parameter")
        unknown_grouped = set(grouped_parameter_ids) - parameter_ids
        if unknown_grouped:
            names = ", ".join(sorted(unknown_grouped))
            raise ValueError(f"Parameter groups reference unknown parameters: {names}")
        if self.variadic_input is not None:
            if any(self.variadic_input.index(port.id) is not None for port in self.inputs):
                raise ValueError("Fixed input IDs must not overlap the variadic input family")
            if any(
                self.variadic_input.index(parameter.id) is not None for parameter in self.parameters
            ):
                raise ValueError("Parameter IDs must not overlap the variadic input family")
        if any(not alias.strip() for alias in self.aliases):
            msg = "Node aliases must not be empty"
            raise ValueError(msg)
        _ensure_unique((alias.casefold() for alias in self.aliases), "node alias")

    def input(self, port_id: str) -> InputPortSpec | None:
        fixed = next((port for port in self.inputs if port.id == port_id), None)
        if fixed is not None or self.variadic_input is None:
            return fixed
        index = self.variadic_input.index(port_id)
        return None if index is None else self.variadic_input.port(index)

    def input_ports(
        self,
        connected_port_ids: Iterable[str] = (),
        *,
        include_next_variadic: bool = False,
    ) -> tuple[InputPortSpec, ...]:
        """Return fixed and effective variadic sockets in deterministic numeric order."""

        if self.variadic_input is None:
            return self.inputs
        connected_indexes = {
            index
            for port_id in connected_port_ids
            if (index := self.variadic_input.index(port_id)) is not None
        }
        indexes = set(range(1, self.variadic_input.minimum_count + 1)) | connected_indexes
        if include_next_variadic and indexes <= connected_indexes:
            next_index = 1
            while next_index in connected_indexes:
                next_index += 1
            indexes.add(next_index)
        return (*self.inputs, *(self.variadic_input.port(index) for index in sorted(indexes)))

    def output(self, port_id: str) -> OutputPortSpec | None:
        return next((port for port in self.outputs if port.id == port_id), None)

    def parameter(self, parameter_id: str) -> ParameterSpec | None:
        return next(
            (parameter for parameter in self.parameters if parameter.id == parameter_id), None
        )

    def parameter_values(
        self, literals: Mapping[str, object]
    ) -> tuple[dict[str, ParameterValue], list[str]]:
        values: dict[str, ParameterValue] = {}
        errors: list[str] = []
        known = {parameter.id for parameter in self.parameters}
        for unknown_id in sorted(set(literals) - known):
            errors.append(f"unknown parameter {unknown_id!r}")
        for parameter in self.parameters:
            value = literals.get(parameter.id, parameter.default)
            error = parameter.validate(value)
            if error is not None:
                if error == "must be finite":
                    errors.append(f"parameter {parameter.id!r}: {parameter.id} must be finite")
                else:
                    errors.append(f"parameter {parameter.id!r} {error}")
                values[parameter.id] = parameter.default
                continue
            values[parameter.id] = _as_parameter_value(value)
        if not errors and self.parameter_validator is not None:
            errors.extend(self.parameter_validator(values))
        return values, errors

    def port_type(
        self,
        port_id: str,
        *,
        is_output: bool,
        parameters: Mapping[str, ParameterValue],
    ) -> PortTypeExpression:
        if self.port_type_resolver is not None:
            return self.port_type_resolver(port_id, is_output, parameters)
        port = self.output(port_id) if is_output else self.input(port_id)
        if port is None:
            msg = f"Unknown port {port_id!r} on {self.type_id}"
            raise KeyError(msg)
        return port.value_type

    def required_inputs(self, parameters: Mapping[str, ParameterValue]) -> frozenset[str]:
        if self.required_input_resolver is not None:
            required = set(self.required_input_resolver(parameters))
        else:
            required = {port.id for port in self.inputs if port.required}
        if self.variadic_input is not None:
            required.update(
                self.variadic_input.port(index).id
                for index in range(1, self.variadic_input.minimum_count + 1)
            )
        return frozenset(required)


class ExpectedNodeError(Exception):
    """Recoverable node failure that should produce structured ``NoData`` output."""

    def __init__(self, code: str, message: str, *, details: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _validate_stable_id(value: str, kind: str) -> None:
    if not _STABLE_ID.fullmatch(value):
        msg = f"Invalid stable {kind} ID: {value!r}"
        raise ValueError(msg)


def _ensure_unique(values: Iterable[str], kind: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            msg = f"Duplicate {kind} ID: {value!r}"
            raise ValueError(msg)
        seen.add(value)


def _resolve_parameter_socket(
    parameter: ParameterSpec,
    execution_kind: ExecutionKind,
    cache_policy: CachePolicy,
) -> ParameterSpec:
    """Expose live scalar controls while excluding structural and configuration settings."""

    connectable = parameter.connectable
    if connectable is None:
        connectable = (
            parameter.value_type in {PortType.FLOAT, PortType.INT, PortType.BOOL}
            and parameter.update_mode is ParameterUpdateMode.LIVE
            and execution_kind not in {ExecutionKind.SOURCE, ExecutionKind.VISUALIZER}
            and cache_policy is not CachePolicy.STATIC
        )
    connected_port_type = parameter.connected_port_type
    if connectable:
        if parameter.value_type in {PortType.FLOAT, PortType.INT}:
            # Numeric parameter cables intentionally share one modulation type. INT literals are
            # rounded after transport, which lets every numeric scalar output drive every numeric
            # control without littering graphs with conversion nodes.
            connected_port_type = PortType.FLOAT
        elif parameter.value_type is PortType.BOOL:
            connected_port_type = PortType.BOOL
    else:
        connected_port_type = None
    return replace(
        parameter,
        connectable=connectable,
        connected_port_type=connected_port_type,
    )


def _matches_port_type(value: object, value_type: PortType) -> bool:
    if value_type is PortType.FLOAT:
        return isinstance(value, float)
    if value_type is PortType.INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is PortType.BOOL:
        return isinstance(value, bool)
    if value_type is PortType.STRING:
        return isinstance(value, str)
    if value_type is PortType.COLOR:
        return isinstance(value, ColorValue)
    if value_type is PortType.MATRIX:
        return isinstance(value, NumericMatrix)
    return False


def _as_parameter_value(value: object) -> ParameterValue:
    if isinstance(value, (str, bool, ColorValue, NumericMatrix, float)):
        return value
    if isinstance(value, int):
        return value
    msg = f"Unsupported parameter value: {value!r}"
    raise TypeError(msg)
