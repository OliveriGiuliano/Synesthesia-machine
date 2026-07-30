"""Final client-facing contract and source-component reset tests."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.contracts import (
    ImagePreview,
    NoteActivity,
    NotePreview,
    PortType,
    freeze_uint8_preview,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.nodes import ExecutionKind, ResetReason
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import Scheduler
from tests.phase1.helpers import ProbeRuntime, RuntimeCounters, make_definition


def test_preview_contracts_are_compact_immutable_and_deterministic() -> None:
    node_id = UUID("00000000-0000-0000-0000-000000000301")
    mutable = np.zeros((2, 3, 3), dtype=np.uint8)
    data = freeze_uint8_preview(mutable)
    preview = ImagePreview(node_id, 1, 2, 3, 2, 3, data)
    notes = NotePreview(
        node_id,
        1,
        2,
        (NoteActivity(0, 60, 100), NoteActivity(1, 64, 80)),
    )

    mutable[...] = 255
    assert not preview.data.flags.writeable
    assert np.count_nonzero(preview.data) == 0
    assert notes.notes[0].note == 60
    with pytest.raises(ValueError, match="deterministic"):
        NotePreview(node_id, 1, 1, tuple(reversed(notes.notes)))


def test_scheduler_resets_only_state_owners_in_selected_source_component() -> None:
    source_a = UUID("00000000-0000-0000-0000-00000000030a")
    state_a = UUID("00000000-0000-0000-0000-00000000031a")
    source_b = UUID("00000000-0000-0000-0000-00000000030b")
    state_b = UUID("00000000-0000-0000-0000-00000000031b")
    resets: dict[UUID, list[ResetReason]] = {}
    counters: dict[UUID, RuntimeCounters] = {}

    class ResetProbe(ProbeRuntime):
        def reset(self, reason: ResetReason) -> None:
            resets.setdefault(self.node_id, []).append(reason)

    def factory(node_id: UUID) -> ResetProbe:
        return ResetProbe(node_id, counters)

    source_definition = make_definition("test.phase3_source", execution_kind=ExecutionKind.SOURCE)
    state_definition = make_definition(
        "test.phase3_state", input_type=PortType.FLOAT, execution_kind=ExecutionKind.STATEFUL
    )
    state_definition = replace(state_definition, runtime_factory=factory)
    registry = NodeRegistry((source_definition, state_definition))
    document = GraphDocument()
    document.add_node(source_definition.type_id, node_id=source_a)
    document.add_node(state_definition.type_id, node_id=state_a)
    document.add_node(source_definition.type_id, node_id=source_b)
    document.add_node(state_definition.type_id, node_id=state_b)
    document.add_connection(source_a, "value", state_a, "value")
    document.add_connection(source_b, "value", state_b, "value")
    plan = GraphCompiler(registry).compile(document.snapshot()).plan
    assert plan is not None
    scheduler = Scheduler(plan)

    scheduler.reset_source(source_a, ResetReason.SOURCE_RESTARTED)

    assert resets == {state_a: [ResetReason.SOURCE_RESTARTED]}
