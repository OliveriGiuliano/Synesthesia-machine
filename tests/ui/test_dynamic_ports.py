"""Shared musical controls and general indexed-variadic graph contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QSpinBox

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    FrameContext,
    MidiNoteKey,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument, ValidationReport
from synesthesia_machine.midi import CUSTOM_SCALE_ID
from synesthesia_machine.nodes import (
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    NodeRegistry,
    OutputPortSpec,
    ParameterGroupSpec,
    ParameterSpec,
    ResetReason,
    VariadicInputSpec,
)
from synesthesia_machine.nodes.synesthesia import (
    CHANNEL_TO_PITCH_TYPE_ID,
    COMMON_MUSICAL_PARAMETER_IDS,
    common_musical_parameter_specs,
    create_synesthesia_definitions,
    midi_state_from_candidates,
    resolve_common_musical_settings,
)
from synesthesia_machine.persistence import (
    copy_fragment,
    fragment_from_json,
    fragment_to_json,
    graph_from_json,
    graph_to_json,
)
from synesthesia_machine.runtime import PortKey, Scheduler
from synesthesia_machine.ui.musical_controls import MusicalParameterEditor
from synesthesia_machine.ui.view_models import project_graph

SOURCE_A = UUID("00000000-0000-0000-0000-000000000601")
SOURCE_B = UUID("00000000-0000-0000-0000-000000000602")
SOURCE_C = UUID("00000000-0000-0000-0000-000000000603")
MERGE = UUID("00000000-0000-0000-0000-000000000604")
CLOCK = UUID("00000000-0000-0000-0000-000000000605")


class _ConstantRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {"value": {SOURCE_A: 1.0, SOURCE_B: 2.0, SOURCE_C: 10.0}[self.node_id]}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


class _VariadicSumRuntime:
    observed_input_order: tuple[str, ...] = ()

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del parameters, context
        type(self).observed_input_order = tuple(inputs)
        values: list[float] = []
        for value in inputs.values():
            if not isinstance(value, float):
                raise TypeError("Expected float variadic input")
            values.append(value)
        return {"value": sum(values)}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def _variadic_registry() -> NodeRegistry:
    source = NodeDefinition(
        "test.dynamic_source",
        1,
        "Source",
        "Test",
        "Static test source.",
        (),
        (OutputPortSpec("value", "Value", PortType.FLOAT),),
        (),
        ExecutionKind.STATELESS,
        _ConstantRuntime,
    )
    variadic = NodeDefinition(
        "test.dynamic_variadic",
        1,
        "Variadic sum",
        "Test",
        "General variadic test node.",
        (),
        (OutputPortSpec("value", "Value", PortType.FLOAT),),
        (),
        ExecutionKind.STATELESS,
        _VariadicSumRuntime,
        variadic_input=VariadicInputSpec("item", "Item", PortType.FLOAT, minimum_count=2),
    )
    return NodeRegistry((source, variadic))


def _variadic_document(*, include_third: bool = True) -> GraphDocument:
    document = GraphDocument()
    for node_id in (SOURCE_A, SOURCE_B, SOURCE_C):
        document.add_node("test.dynamic_source", node_id=node_id)
    document.add_node("test.dynamic_variadic", node_id=MERGE)
    document.add_connection(SOURCE_A, "value", MERGE, "item_1")
    document.add_connection(SOURCE_B, "value", MERGE, "item_2")
    if include_third:
        document.add_connection(SOURCE_C, "value", MERGE, "item_10")
    return document


def _context() -> FrameContext:
    return FrameContext(CLOCK, 1, 0, 0.0, 1, None, False)


def test_common_musical_metadata_resolution_and_channel_to_pitch_are_stable() -> None:
    specs = common_musical_parameter_specs()
    assert tuple(spec.id for spec in specs) == COMMON_MUSICAL_PARAMETER_IDS
    assert tuple(spec.default for spec in specs) == (
        "C",
        "chromatic",
        "111111111111",
        0,
        127,
        1,
        16,
        1,
        127,
    )
    definition = create_synesthesia_definitions()[0]
    assert definition.type_id == CHANNEL_TO_PITCH_TYPE_ID
    assert definition.parameters[: len(specs)] == specs
    assert definition.parameter_groups[0].parameter_ids == COMMON_MUSICAL_PARAMETER_IDS

    parameters, errors = definition.parameter_values(
        {
            "root_pitch_class": "D",
            "scale": "major",
            "midi_minimum": 60,
            "midi_maximum": 72,
            "midi_channel": 16,
            "maximum_polyphony": 3,
            "minimum_velocity": 10,
            "maximum_velocity": 100,
        }
    )
    assert errors == []
    settings = resolve_common_musical_settings(parameters)
    assert settings.selector.allowed_notes == (61, 62, 64, 66, 67, 69, 71)
    assert settings.midi_channel == 15
    assert (settings.maximum_polyphony, settings.minimum_velocity, settings.maximum_velocity) == (
        3,
        10,
        100,
    )
    state = midi_state_from_candidates(
        ((64, 0.2), (60, 0.5), (64, 0.75), (67, 0.75)),
        settings=settings,
        context=_context(),
        source_node_id=MERGE,
    )
    assert state.notes == {
        MidiNoteKey(15, 64): 78,
        MidiNoteKey(15, 67): 78,
        MidiNoteKey(15, 60): 55,
    }
    assert state.context == _context()
    assert state.source_node_id == MERGE


def test_parameter_group_metadata_rejects_unknown_or_duplicate_members() -> None:
    definition = create_synesthesia_definitions()[0]
    assert definition.parameter_groups[0].id == "musical"
    assert len(set(definition.parameter_groups[0].parameter_ids)) == 9

    with pytest.raises(ValueError, match="Duplicate grouped parameter ID"):
        ParameterGroupSpec("invalid", "Invalid", ("known", "known"))

    parameter = ParameterSpec("known", "Known", PortType.INT, 0)
    with pytest.raises(ValueError, match="reference unknown parameters: missing"):
        replace(
            definition,
            parameters=(parameter,),
            parameter_groups=(ParameterGroupSpec("invalid", "Invalid", ("missing",)),),
        )
    with pytest.raises(ValueError, match="Duplicate grouped parameter ID"):
        replace(
            definition,
            parameters=(parameter,),
            parameter_groups=(
                ParameterGroupSpec("first", "First", ("known",)),
                ParameterGroupSpec("second", "Second", ("known",)),
            ),
        )


def test_variadic_metadata_validation_and_effective_socket_projection() -> None:
    definition = _variadic_registry().require("test.dynamic_variadic")
    assert definition.input("item_10") is not None
    assert definition.input("item_0") is None
    assert definition.input("item_01") is None
    assert tuple(port.id for port in definition.input_ports()) == ("item_1", "item_2")
    assert tuple(
        port.id
        for port in definition.input_ports(
            ("item_1", "item_2", "item_10"), include_next_variadic=True
        )
    ) == ("item_1", "item_2", "item_3", "item_10")

    with pytest.raises(ValueError, match="minimum_count must be at least 1"):
        VariadicInputSpec("item", "Item", PortType.FLOAT, minimum_count=0)
    with pytest.raises(ValueError, match="must not overlap"):
        replace(
            definition,
            inputs=(InputPortSpec("item_1", "Item 1", PortType.FLOAT),),
        )
    with pytest.raises(ValueError, match="Parameter IDs must not overlap"):
        replace(
            definition,
            parameters=(ParameterSpec("item_1", "Item 1", PortType.INT, 0),),
        )


def test_variadic_compiler_requires_minimum_and_binds_numeric_order() -> None:
    registry = _variadic_registry()
    incomplete = _variadic_document(include_third=False)
    connection = incomplete.incoming_connection(MERGE, "item_2")
    assert connection is not None
    incomplete.remove_connection(connection.id)
    missing = GraphCompiler(registry).compile(incomplete.snapshot())
    assert missing.plan is None
    assert any(
        issue.code == "required_input_missing" and issue.port_id == "item_2"
        for issue in missing.report.errors
    )

    document = _variadic_document()
    result = GraphCompiler(registry).compile(document.snapshot())
    assert result.report.is_valid and result.plan is not None
    compiled = result.plan.node(MERGE)
    assert compiled is not None
    assert tuple(compiled.input_bindings) == ("item_1", "item_2", "item_10")
    assert tuple(compiled.input_types) == ("item_1", "item_2", "item_10")

    tick = Scheduler(result.plan).execute_tick(_context())
    assert tick.errors == ()
    assert tick.values[PortKey(MERGE, "value")] == 13.0
    assert _VariadicSumRuntime.observed_input_order == ("item_1", "item_2", "item_10")


def test_variadic_rejects_unknown_ids_and_preserves_concrete_socket_cardinality() -> None:
    registry = _variadic_registry()
    document = _variadic_document(include_third=False)
    document.add_connection(SOURCE_C, "value", MERGE, "item_0")
    invalid = GraphCompiler(registry).compile(document.snapshot())
    assert any(issue.code == "unknown_input_port" for issue in invalid.report.errors)

    document.add_connection(SOURCE_C, "value", MERGE, "item_1")
    incoming = [
        connection
        for connection in document.connections
        if connection.destination_node_id == MERGE and connection.destination_port_id == "item_1"
    ]
    assert len(incoming) == 1
    assert incoming[0].source_node_id == SOURCE_C


def test_variadic_json_clipboard_and_ui_projection_preserve_concrete_ids() -> None:
    registry = _variadic_registry()
    snapshot = _variadic_document().snapshot()
    restored = graph_from_json(graph_to_json(snapshot), registry)
    assert restored == replace(snapshot, revision=0)

    fragment = copy_fragment(snapshot, {SOURCE_A, SOURCE_B, SOURCE_C, MERGE})
    restored_fragment = fragment_from_json(fragment_to_json(fragment))
    assert restored_fragment == fragment
    assert {connection.destination_port_id for connection in restored_fragment.connections} == {
        "item_1",
        "item_2",
        "item_10",
    }

    projection = project_graph(restored, registry, ValidationReport())
    merge = next(node for node in projection.nodes if node.node_id == MERGE)
    assert tuple(port.port_id for port in merge.inputs) == (
        "item_1",
        "item_2",
        "item_3",
        "item_10",
    )
    assert tuple(port.connected for port in merge.inputs) == (True, True, False, True)


def test_shared_musical_view_model_and_editor_cover_ranges_custom_mode_and_edits(
    qapp: QApplication,
) -> None:
    del qapp
    registry = create_application_registry()
    document = GraphDocument()
    node_id = document.add_node(CHANNEL_TO_PITCH_TYPE_ID)
    compilation = GraphCompiler(registry).compile(document.snapshot())
    projection = project_graph(document.snapshot(), registry, compilation.report)
    node = next(node for node in projection.nodes if node.node_id == node_id)
    assert len(node.parameter_groups) == 1
    assert tuple(parameter.spec.id for parameter in node.parameter_groups[0].parameters) == (
        COMMON_MUSICAL_PARAMETER_IDS
    )

    edits: list[tuple[str, object]] = []
    editor = MusicalParameterEditor(
        node.parameter_groups[0], lambda parameter_id, value: edits.append((parameter_id, value))
    )
    root = editor.editors["root_pitch_class"]
    scale = editor.editors["scale"]
    custom = editor.editors["custom_scale_mask"]
    midi_minimum = editor.editors["midi_minimum"]
    midi_channel = editor.editors["midi_channel"]
    assert isinstance(root, QComboBox)
    assert isinstance(scale, QComboBox)
    assert isinstance(custom, QLineEdit) and not custom.isEnabled()
    assert isinstance(midi_minimum, QSpinBox) and midi_minimum.minimum() == 0
    assert midi_minimum.maximum() == 127
    assert isinstance(midi_channel, QSpinBox) and midi_channel.minimum() == 1
    assert midi_channel.maximum() == 16

    scale.setCurrentIndex(scale.findData(CUSTOM_SCALE_ID))
    assert edits[-1] == ("scale", CUSTOM_SCALE_ID)
    assert custom.isEnabled()
    custom.setText("101010101010")
    custom.editingFinished.emit()
    assert edits[-1] == ("custom_scale_mask", "101010101010")
    editor.close()


def test_common_validation_feedback_reaches_compiler_report() -> None:
    registry = create_application_registry()
    document = GraphDocument()
    node_id = document.add_node(
        CHANNEL_TO_PITCH_TYPE_ID,
        parameters={"midi_minimum": 72, "midi_maximum": 60},
    )
    result = GraphCompiler(registry).compile(document.snapshot())
    assert result.plan is None
    assert any(
        issue.node_id == node_id
        and issue.code == "invalid_parameter"
        and "minimum cannot exceed" in issue.message
        for issue in result.report.errors
    )
