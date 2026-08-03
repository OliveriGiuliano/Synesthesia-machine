"""Stable metadata and execution contracts for headless node implementations."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from synesthesia_machine.contracts.engine_client import MidiOutputStatus
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

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.name):
            msg = f"Invalid type-variable name: {self.name!r}"
            raise ValueError(msg)


type PortTypeExpression = PortType | TypeVariable


@dataclass(frozen=True, slots=True)
class InputPortSpec:
    id: str
    label: str
    value_type: PortTypeExpression
    required: bool = True

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "input port")


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
    connectable: bool = False
    connected_port_type: PortType | None = None
    update_mode: ParameterUpdateMode = ParameterUpdateMode.LIVE

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "parameter")
        error = self.validate(self.default)
        if error is not None:
            msg = f"Invalid default for parameter {self.id!r}: {error}"
            raise ValueError(msg)
        if self.connectable and self.connected_port_type is None:
            msg = f"Connectable parameter {self.id!r} requires connected_port_type"
            raise ValueError(msg)

    def validate(self, value: object) -> str | None:
        if not _matches_port_type(value, self.value_type):
            return f"expected {self.value_type.value}, got {type(value).__name__}"
        if self.choices and value not in self.choices:
            return f"expected one of {self.choices!r}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                return f"must be at least {self.minimum}"
            if self.maximum is not None and value > self.maximum:
                return f"must be at most {self.maximum}"
        return None


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


class ResetReason(StrEnum):
    PLAN_REPLACED = "PLAN_REPLACED"
    SOURCE_RESTARTED = "SOURCE_RESTARTED"
    SEEK = "SEEK"
    PARAMETER_CHANGED = "PARAMETER_CHANGED"
    CLOCK_CHANGED = "CLOCK_CHANGED"
    ENGINE_RESTARTED = "ENGINE_RESTARTED"


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

    def __post_init__(self) -> None:
        if not _TYPE_ID.fullmatch(self.type_id):
            msg = f"Invalid node type ID: {self.type_id!r}"
            raise ValueError(msg)
        if self.implementation_version < 1:
            msg = "implementation_version must be at least 1"
            raise ValueError(msg)
        _ensure_unique((port.id for port in self.inputs), "input port")
        _ensure_unique((port.id for port in self.outputs), "output port")
        _ensure_unique((parameter.id for parameter in self.parameters), "parameter")
        if any(not alias.strip() for alias in self.aliases):
            msg = "Node aliases must not be empty"
            raise ValueError(msg)
        _ensure_unique((alias.casefold() for alias in self.aliases), "node alias")

    def input(self, port_id: str) -> InputPortSpec | None:
        return next((port for port in self.inputs if port.id == port_id), None)

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
            return frozenset(self.required_input_resolver(parameters))
        return frozenset(port.id for port in self.inputs if port.required)


@dataclass(frozen=True, slots=True)
class NodeExecutionError:
    node_id: UUID
    code: str
    message: str
    details: str | None
    recoverable: bool
    tick_index: int | None


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
