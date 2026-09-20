"""Publish generation stamping and panic watermarks at the facade boundary (ADR-0022)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from tests.support.graph_factories import frame_context, make_definition

from synesthesia_machine.contracts import (
    FrameContext,
    MidiNoteKey,
    MidiStateFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes import (
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    NodeExecutionContract,
    NodePresentationIntent,
    OutputPortSpec,
    ResetReason,
)
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import PortKey
from synesthesia_machine.runtime.engine_facade import EngineFacade

SOURCE_ID = UUID("00000000-0000-0000-0000-000000005001")
PROBE_ID = UUID("00000000-0000-0000-0000-000000005002")


@dataclass
class _GenerationProbe:
    node_id: UUID
    seen_generations: list[int] = field(default_factory=list)
    panicked_generations: list[int] = field(default_factory=list)
    close_count: int = 0

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters
        self.seen_generations.append(context.publish_generation)
        return {}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def panic(self, publish_generation: int) -> None:
        self.panicked_generations.append(publish_generation)

    def close(self) -> None:
        self.close_count += 1


def _probe_definition(probes: list[_GenerationProbe]) -> NodeDefinition:
    def factory(node_id: UUID) -> _GenerationProbe:
        probe = _GenerationProbe(node_id)
        probes.append(probe)
        return probe

    return NodeDefinition(
        execution=NodeExecutionContract(
            type_id="test.generation_probe",
            implementation_version=1,
            parameters=(),
            inputs=(InputPortSpec("value", "Value", PortType.MIDI_STATE),),
            outputs=(OutputPortSpec("value", "Value", PortType.MIDI_STATE),),
            execution_kind=ExecutionKind.SINK,
            runtime_factory=factory,
        ),
        presentation=NodePresentationIntent(
            display_name="test.generation_probe",
            category="Test",
            description="Records the publish generation of each tick.",
        ),
    )


def _source_definition() -> NodeDefinition:
    return make_definition(
        "test.midi_source",
        output_type=PortType.MIDI_STATE,
        execution_kind=ExecutionKind.SOURCE,
    )


def _state() -> MidiStateFrame:
    return MidiStateFrame(
        {MidiNoteKey(0, 60): 100},
        frame_context(clock_id=SOURCE_ID),
        SOURCE_ID,
    )


def _document() -> GraphDocument:
    document = GraphDocument()
    document.add_node("test.midi_source", node_id=SOURCE_ID)
    document.add_node("test.generation_probe", node_id=PROBE_ID)
    document.add_connection(SOURCE_ID, "value", PROBE_ID, "value")
    return document


def test_facade_stamps_ticks_with_monotonic_publish_generation() -> None:
    # The facade, not the sources, owns tick ordering: a source-built context
    # carries generation 0, and every value produced by a tick inherits the
    # stamped tick generation through its context.
    probes: list[_GenerationProbe] = []
    registry = NodeRegistry((_source_definition(), _probe_definition(probes)))
    facade = EngineFacade(registry)
    try:
        assert facade.activate(_document().snapshot()).plan is not None
        source_values = {PortKey(SOURCE_ID, "value"): _state()}
        facade.tick(frame_context(clock_id=SOURCE_ID), source_values=source_values)
        facade.tick(frame_context(clock_id=SOURCE_ID, tick_index=2), source_values=source_values)
        facade.tick(frame_context(clock_id=SOURCE_ID, tick_index=3), source_values=source_values)
        assert len(probes) == 1
        assert probes[0].seen_generations == [1, 2, 3]
    finally:
        facade.close()
    assert probes[0].close_count == 1


def test_publish_generation_stays_monotonic_across_plan_replacement() -> None:
    # Plan replacement can preserve node runtimes: their panic watermarks must
    # stay comparable with the generations stamped by ticks of both plans, so
    # the counter lives on the facade, not on each scheduler.
    probes: list[_GenerationProbe] = []
    registry = NodeRegistry((_source_definition(), _probe_definition(probes)))
    facade = EngineFacade(registry)
    try:
        assert facade.activate(_document().snapshot()).plan is not None
        facade.tick(
            frame_context(clock_id=SOURCE_ID),
            source_values={PortKey(SOURCE_ID, "value"): _state()},
        )
        facade.panic()
        assert len(probes) == 1
        assert probes[0].panicked_generations == [1]

        # An identical re-activation preserves the probe runtime.
        assert facade.activate(_document().snapshot()).plan is not None
        assert len(probes) == 1
        facade.tick(
            frame_context(clock_id=SOURCE_ID, tick_index=2),
            source_values={PortKey(SOURCE_ID, "value"): _state()},
        )
        assert probes[0].seen_generations == [1, 2]
        assert probes[0].seen_generations[1] > probes[0].panicked_generations[0]
    finally:
        facade.close()
