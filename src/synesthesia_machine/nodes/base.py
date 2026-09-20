"""Stable metadata and execution contracts for headless node implementations."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    DeviceKind,
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
    NoData,
    NumericMatrix,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.nodes.migrations import NodeMigration

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
    TIMESTAMP = "TIMESTAMP"
    LOOP_RANGE = "LOOP_RANGE"


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
    step: int | None = None
    device_kind: DeviceKind | None = None

    def __post_init__(self) -> None:
        _validate_stable_id(self.id, "parameter")
        if self.device_kind is not None and self.value_type is not PortType.STRING:
            raise ValueError("Device parameters must use the STRING value type")
        if self.minimum is not None or self.maximum is not None:
            if self.value_type not in {PortType.FLOAT, PortType.INT}:
                raise ValueError("Parameter bounds require a numeric value type")
            for bound in (self.minimum, self.maximum):
                if bound is not None and (
                    isinstance(bound, bool) or not math.isfinite(float(bound))
                ):
                    raise ValueError("Parameter bounds must be finite numbers")
                if (
                    bound is not None
                    and self.value_type is PortType.INT
                    and not isinstance(bound, int)
                ):
                    raise ValueError("INT parameter bounds must be integers")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("Parameter minimum cannot exceed maximum")
        if self.step is not None:
            if self.value_type is not PortType.INT:
                raise ValueError("Parameter steps currently require an INT value type")
            if self.step < 1:
                raise ValueError("Parameter step must be a positive integer")
            if self.choices:
                raise ValueError("Parameter step and explicit choices cannot be combined")
        error = self.validate(self.default)
        if error is not None:
            msg = f"Invalid default for parameter {self.id!r}: {error}"
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
            if self.step is not None and (value - self._step_origin()) % self.step != 0:
                return f"must use increments of {self.step} from {self._step_origin()}"
        return None

    def sanitize_value(self, value: object) -> ParameterValue:
        """Clamp and snap an authored value to this parameter's declared constraints."""

        converted = self._check_literal(value)
        if self.value_type is PortType.FLOAT:
            # ``_check_literal`` already enforced the type; repeat the check so
            # the numeric narrowing below is visible to the type checker.
            if not isinstance(converted, float):
                msg = f"expected {self.value_type.value}, got {type(converted).__name__}"
                raise ValueError(msg)
            if not math.isfinite(converted):
                raise ValueError("must be finite")
            numeric = float(converted)
            if self.minimum is not None:
                numeric = max(numeric, float(self.minimum))
            if self.maximum is not None:
                numeric = min(numeric, float(self.maximum))
            if self.choices:
                numeric_choices = tuple(
                    choice for choice in self.choices if isinstance(choice, float)
                )
                if numeric_choices:
                    numeric = min(
                        numeric_choices,
                        key=lambda choice: abs(choice - numeric),
                    )
            converted = numeric
        elif self.value_type is PortType.INT:
            if not isinstance(converted, int) or isinstance(converted, bool):
                msg = f"expected {self.value_type.value}, got {type(converted).__name__}"
                raise ValueError(msg)
            numeric = int(converted)
            if self.minimum is not None:
                numeric = max(numeric, int(self.minimum))
            if self.maximum is not None:
                numeric = min(numeric, int(self.maximum))
            if self.step is not None:
                origin = self._step_origin()
                step_index = math.floor((numeric - origin) / self.step + 0.5)
                if self.minimum is not None:
                    step_index = max(
                        step_index,
                        math.ceil((int(self.minimum) - origin) / self.step),
                    )
                if self.maximum is not None:
                    step_index = min(
                        step_index,
                        math.floor((int(self.maximum) - origin) / self.step),
                    )
                numeric = origin + step_index * self.step
            if self.choices:
                integer_choices = tuple(
                    choice
                    for choice in self.choices
                    if isinstance(choice, int) and not isinstance(choice, bool)
                )
                if integer_choices:
                    numeric = min(
                        integer_choices,
                        key=lambda choice: abs(choice - numeric),
                    )
            converted = numeric
        error = self.validate(converted)
        if error is not None:
            raise ValueError(error)
        return converted

    def _step_origin(self) -> int:
        return int(self.minimum) if self.minimum is not None else 0

    def _check_literal(self, value: object) -> ParameterValue:
        """Strict type gate for authored and default values.

        This is the single type check of the value ladder: ``validate``
        reports on the same predicate (``_matches_port_type``), and both
        ``sanitize_value`` and ``connected_value`` pass through it before
        applying constraints, so whatever the ladder produces is what
        ``validate`` accepts — asserted once by the ladder consistency
        test rather than re-derived at each call site.
        """

        if self.value_type is PortType.FLOAT and isinstance(value, float):
            return value
        if (
            self.value_type is PortType.INT
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            return value
        if self.value_type is PortType.BOOL and isinstance(value, bool):
            return value
        if self.value_type is PortType.STRING and isinstance(value, str):
            return value
        if self.value_type is PortType.COLOR and isinstance(value, ColorValue):
            return value
        if self.value_type is PortType.MATRIX and isinstance(value, NumericMatrix):
            return value
        msg = f"expected {self.value_type.value}, got {type(value).__name__}"
        raise ValueError(msg)

    def connected_value(self, value: object) -> float | int | bool:
        """Coerce and bound a live socket value to this parameter's literal contract."""

        converted = self._check_live_scalar(value)
        # ``_check_live_scalar`` emits scalars only, and ``sanitize_value`` maps
        # scalars to scalars, so the ladder cannot widen the value's type.
        return cast("float | int | bool", self.sanitize_value(converted))

    def _check_live_scalar(self, value: object) -> float | int | bool:
        """Coerce a live socket value to this parameter's declared scalar type.

        Numeric parameter cables intentionally share one modulation type:
        int values convert to float, and float values round to int, which
        lets every numeric scalar output drive every numeric control without
        littering graphs with conversion nodes.
        """

        if self.value_type is PortType.FLOAT:
            if isinstance(value, float):
                return value
            if isinstance(value, int) and not isinstance(value, bool):
                return float(value)
            msg = f"expected numeric scalar, got {type(value).__name__}"
            raise ValueError(msg)
        if self.value_type is PortType.INT:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, float):
                if not math.isfinite(value):
                    msg = "expected a finite numeric scalar"
                    raise ValueError(msg)
                return round(value)
            msg = f"expected numeric scalar, got {type(value).__name__}"
            raise ValueError(msg)
        if self.value_type is PortType.BOOL:
            if isinstance(value, bool):
                return value
            msg = f"expected BOOL, got {type(value).__name__}"
            raise ValueError(msg)
        msg = f"{self.value_type.value} parameters do not accept live scalar input"
        raise ValueError(msg)


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


class PreviewDock(StrEnum):
    """The window preview dock a display visualizer feeds.

    The editor keeps one display visualizer per dock: adding one removes
    the dock's incumbent (e.g. a channel display takes the image dock's
    slot from an image display).
    """

    IMAGE = "image"
    NOTE = "note"


class CachePolicy(StrEnum):
    """How the compiler treats a node's static-ness.

    ``AUTO`` (the default) and ``STATIC`` compile identically: a node is static
    (its outputs cached for the plan's lifetime) when it is not on a source
    clock, and live otherwise. ``NEVER`` is the only policy that forces live
    execution. ``STATIC`` is an authoring assertion that the node's outputs are
    source-independent: besides the same execution semantics as ``AUTO``, it
    makes LIVE scalar parameters non-connectable by default, since driving a
    statically cached node with cable values would contradict the assertion
    (declare ``connectable=True`` on a specific parameter to allow it).
    """

    AUTO = "AUTO"
    NEVER = "NEVER"
    STATIC = "STATIC"


class NodeRuntime(Protocol):
    """The lifecycle a node author implements for the engine.

    The scheduler enforces the ``NoData`` contract around ``process``: unless
    the node's execution contract declares ``handles_no_data=True``,
    ``process`` is never invoked while any input is ``NoData`` — the scheduler
    publishes ``NoData`` for every declared output instead. A ``process`` that
    does run must return a value for every declared output port: a port missing
    from the returned mapping is filled with ``NoData`` by the scheduler,
    indistinguishable from an intentional ``NoData``, so a runtime that omits
    a port hides its own failure.
    """

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
    def panic(self, publish_generation: int) -> None: ...


@runtime_checkable
class MidiOutputStatusProvider(Protocol):
    def midi_output_status(self) -> MidiOutputStatus: ...


@runtime_checkable
class NodeMemoryDiagnosticProvider(Protocol):
    def node_memory_diagnostic(self) -> NodeMemoryDiagnostic: ...


class StatelessRuntime:
    """Lifecycle skeleton for runtimes that hold no state between ticks.

    Concrete stateless nodes subclass this (or one of the adapters below)
    and implement only ``process``; a lifecycle change is one edit here.
    """

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class NoDataRuntime(StatelessRuntime):
    """Scheduler placeholder that publishes ``NoData`` for its declared outputs.

    Source nodes use this as the safe default: the scheduler runs it until
    source construction (or a restart) replaces the node's outputs.
    """

    def __init__(self, node_id: UUID, output_ports: Sequence[str]) -> None:
        super().__init__(node_id)
        self._output_ports = tuple(output_ports)

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {port: NoData for port in self._output_ports}


type PureFunctionProcessor = Callable[
    [UUID, Mapping[str, RuntimeValue], Mapping[str, ParameterValue], FrameContext],
    Mapping[str, RuntimeValue],
]


class PureFunctionRuntime(StatelessRuntime):
    """Stateless runtime wrapping one pure function behind the error contract.

    ``processor`` performs the node's computation - input extraction, the pure
    algorithm call, and the output mapping - and returns the output mapping.
    A failure raising one of ``exceptions`` becomes an ``ExpectedNodeError``
    carrying the node's stable ``error_code``.
    """

    def __init__(
        self,
        node_id: UUID,
        *,
        processor: PureFunctionProcessor,
        error_code: str,
        exceptions: Sequence[type[Exception]] = (ValueError,),
    ) -> None:
        super().__init__(node_id)
        self._processor = processor
        self._error_code = error_code
        self._exceptions = tuple(exceptions)

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        try:
            return self._processor(self.node_id, inputs, parameters, context)
        except self._exceptions as error:
            raise ExpectedNodeError(self._error_code, str(error)) from error


def require_same_clock(first: FrameContext, second: FrameContext, message: str) -> None:
    """Raise ValueError when two frame contexts belong to different source clocks."""

    if first.clock_id != second.clock_id:
        raise ValueError(message)


type RuntimeFactory = Callable[[UUID], NodeRuntime]
type PortTypeResolver = Callable[[str, bool, Mapping[str, ParameterValue]], PortTypeExpression]
type RequiredInputResolver = Callable[[Mapping[str, ParameterValue]], Sequence[str]]
type ParameterValidator = Callable[[Mapping[str, ParameterValue]], Sequence[str]]


@dataclass(frozen=True, slots=True)
class SourceEditorFacts:
    """Source-published facts the editor resolver may use.

    A narrow projection of the engine's published ``SourceStatus``, built by
    the editor: node metadata is headless and must not name the engine's
    device-status record, so resolvers receive only the two facts they can
    act on (nodes README dependency rule).
    """

    file_path: str | None = None
    duration_s: float | None = None


type ParameterEditorResolver = Callable[
    [ParameterSpec, Mapping[str, ParameterValue], SourceEditorFacts | None], ParameterSpec
]


@dataclass(frozen=True, slots=True)
class SourceOutputContract:
    """Declared mapping from a source's published frame onto its output ports.

    Both port IDs must be declared in the owning definition's ``outputs``.
    The graph worker resolves a source's tick values through this contract
    instead of port-name literals, so a source that publishes under other
    names is caught at declaration time rather than silently orphaning its
    outputs at runtime.
    """

    image_port: str
    processed_index_port: str

    def __post_init__(self) -> None:
        _validate_stable_id(self.image_port, "source image port")
        _validate_stable_id(self.processed_index_port, "source processed-index port")
        if self.image_port == self.processed_index_port:
            msg = "Source image and processed-index ports must differ"
            raise ValueError(msg)


# Maps a source node's validated parameter values to its typed source
# configuration value (e.g. ``VideoSourceConfig``); declared by the node
# definition so source construction knowledge stays with the node.
type SourceConfigBuilder = Callable[[Mapping[str, object]], object]


@dataclass(frozen=True, slots=True)
class NodeExecutionContract:
    """Everything the compiler, scheduler, and engine read from a node.

    Compiled plans carry this record rather than the full definition, so
    presentation intent and persistence material never reach the engine's
    working set. Both processes build contracts from their own registries;
    the record itself never crosses the process boundary.

    ``parameters`` holds the socket-resolved specs: construction runs the
    one normalization pass that settles ``connectable`` and
    ``connected_port_type`` from the node's execution kind and cache policy,
    so the specs an author writes are normalized copies, not the stored
    objects.
    """

    type_id: str
    implementation_version: int
    execution_kind: ExecutionKind
    inputs: tuple[InputPortSpec, ...]
    outputs: tuple[OutputPortSpec, ...]
    parameters: tuple[ParameterSpec, ...]
    runtime_factory: RuntimeFactory
    variadic_input: VariadicInputSpec | None = None
    cache_policy: CachePolicy = CachePolicy.AUTO
    handles_no_data: bool = False
    # Opt-in to the NoData input rule stated on the NodeRuntime protocol: with
    # True, process() may be invoked while inputs are NoData, and it must then
    # publish a value for every declared output.
    port_type_resolver: PortTypeResolver | None = None
    required_input_resolver: RequiredInputResolver | None = None
    parameter_validator: ParameterValidator | None = None
    source_outputs: SourceOutputContract | None = None
    source_config_builder: SourceConfigBuilder | None = None

    def __post_init__(self) -> None:
        if not _TYPE_ID.fullmatch(self.type_id):
            msg = f"Invalid node type ID: {self.type_id!r}"
            raise ValueError(msg)
        if self.implementation_version < 1:
            msg = "implementation_version must be at least 1"
            raise ValueError(msg)
        # One normalization pass over the authored specs: this is the only
        # place the record rewrites its inputs, so the authored-vs-stored
        # difference is a single documented policy, not scattered rewrites.
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
        overlapping_inputs = {port.id for port in self.inputs} & {
            parameter.id for parameter in self.parameters
        }
        if overlapping_inputs:
            names = ", ".join(sorted(overlapping_inputs))
            raise ValueError(f"Input ports and parameters share stable IDs: {names}")
        if self.variadic_input is not None:
            if any(self.variadic_input.index(port.id) is not None for port in self.inputs):
                raise ValueError("Fixed input IDs must not overlap the variadic input family")
            if any(
                self.variadic_input.index(parameter.id) is not None for parameter in self.parameters
            ):
                raise ValueError("Parameter IDs must not overlap the variadic input family")
        if self.source_outputs is not None:
            if self.execution_kind is not ExecutionKind.SOURCE:
                msg = f"Non-source node {self.type_id!r} must not declare a source output contract"
                raise ValueError(msg)
            for port_id in (
                self.source_outputs.image_port,
                self.source_outputs.processed_index_port,
            ):
                if self.output(port_id) is None:
                    msg = (
                        f"Source output port {port_id!r} of {self.type_id!r} "
                        "is not declared in its outputs"
                    )
                    raise ValueError(msg)
        if (
            self.source_config_builder is not None
            and self.execution_kind is not ExecutionKind.SOURCE
        ):
            msg = f"Non-source node {self.type_id!r} must not declare a source config builder"
            raise ValueError(msg)

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
            # The requirement is family-level: at least minimum_count of the
            # family's sockets must be connected, any indices. The bare
            # prefix is a synthetic marker (socket ids always carry an index
            # suffix), translated by the compiler into a family count check
            # so that freeing values_1 while values_2+ stay connected does
            # not invalidate the node.
            required.add(self.variadic_input.id_prefix)
        return frozenset(required)


@dataclass(frozen=True, slots=True)
class NodePersistenceDescriptor:
    """The part of a node definition the persistence stack reads.

    The per-version payload steps and the parameter that carries a media
    reference. ``implementation_version`` lives on the execution contract —
    it is the record that declares what a payload version is, and the
    compiler and the migration walker both read it.
    """

    migrations: Mapping[int, NodeMigration] = field(default_factory=dict[int, NodeMigration])
    media_parameter_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodePresentationIntent:
    """The part of a node definition the editor reads.

    Display copy, library-search aliases, preview-dock routing, parameter
    grouping, and the editor resolver that adjusts parameter specs using
    published source facts. Only the editor runs the resolver, and it
    receives ``SourceEditorFacts`` — a narrow projection the editor builds
    from the engine's published status — never the engine status record
    itself, so node metadata stays headless.
    """

    display_name: str
    category: str
    description: str
    aliases: tuple[str, ...] = ()
    preview_dock: PreviewDock | None = None
    parameter_groups: tuple[ParameterGroupSpec, ...] = ()
    parameter_editor_resolver: ParameterEditorResolver | None = None

    def __post_init__(self) -> None:
        if any(not alias.strip() for alias in self.aliases):
            msg = "Node aliases must not be empty"
            raise ValueError(msg)
        _ensure_unique((alias.casefold() for alias in self.aliases), "node alias")


@dataclass(frozen=True, slots=True)
class NodeDefinition:
    """The record a node author writes; what the registry stores.

    The authoring surface is split along the consumers' projections:
    ``execution`` (compiler, scheduler, engine), ``presentation`` (editor),
    ``persistence`` (graph I/O). A node kind omits what it does not use —
    a scalar transform has no persistence descriptor, a source carries all
    three. ``__post_init__`` checks the invariants that span records:
    parameter groups reference declared parameters, the media-reference
    parameter is a declared STRING parameter, and the migration chain
    reaches the contract's implementation version.
    """

    execution: NodeExecutionContract
    presentation: NodePresentationIntent
    persistence: NodePersistenceDescriptor | None = None

    def __post_init__(self) -> None:
        parameter_ids = {parameter.id for parameter in self.execution.parameters}
        grouped_parameter_ids = tuple(
            parameter_id
            for group in self.presentation.parameter_groups
            for parameter_id in group.parameter_ids
        )
        _ensure_unique(grouped_parameter_ids, "grouped parameter")
        unknown_grouped = set(grouped_parameter_ids) - parameter_ids
        if unknown_grouped:
            names = ", ".join(sorted(unknown_grouped))
            raise ValueError(f"Parameter groups reference unknown parameters: {names}")
        persistence = self.persistence
        if persistence is not None and persistence.media_parameter_id is not None:
            parameter = self.execution.parameter(persistence.media_parameter_id)
            if parameter is None:
                msg = (
                    f"Media reference parameter {persistence.media_parameter_id!r} "
                    f"of {self.execution.type_id!r} is not declared in its parameters"
                )
                raise ValueError(msg)
            if parameter.value_type is not PortType.STRING:
                msg = (
                    f"Media reference parameter {persistence.media_parameter_id!r} must be a string"
                )
                raise ValueError(msg)
        _check_migration_chain(
            self.execution.type_id,
            self.execution.implementation_version,
            persistence.migrations if persistence is not None else {},
        )


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


def _check_migration_chain(
    type_id: str,
    implementation_version: int,
    migrations: Mapping[int, NodeMigration],
) -> None:
    """A registered definition must load from every version it can claim.

    Saved payloads start at version 0 (pre-versioning) and walk one step at
    a time, so the migration keys must form a contiguous run that ends
    exactly at ``implementation_version - 1``: a gap strands any older
    payload, and a key at or beyond the current version can never run.
    """
    if not migrations:
        return
    lowest = min(migrations)
    if lowest < 0:
        msg = f"Node {type_id!r} has a migration from a negative version"
        raise ValueError(msg)
    if max(migrations) >= implementation_version:
        msg = (
            f"Node {type_id!r} at implementation_version "
            f"{implementation_version} registers unreachable migration "
            f"{max(migrations)}"
        )
        raise ValueError(msg)
    missing = set(range(lowest, implementation_version)) - set(migrations)
    if missing:
        names = ", ".join(str(version) for version in sorted(missing))
        msg = f"Node {type_id!r} has no migration from version(s) {names}"
        raise ValueError(msg)


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
        elif parameter.connected_port_type is None:
            # Non-scalar connectable parameters must declare the socket type
            # explicitly; the derivation above cannot invent one. This rule
            # moved from ParameterSpec validation to the contract's
            # normalization pass, which is the only place specs are rewritten.
            msg = f"Connectable parameter {parameter.id!r} requires connected_port_type"
            raise ValueError(msg)
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
