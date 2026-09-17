"""Field-parity guards between graph models and their process-safe wire forms.

The wire types live in ``contracts`` (ADR-0005); the mapping lives with the
models it maps (``graph.model``). These tests make drift in either direction a
red test instead of a silent gap across the process seam: adding a field to a
model without a wire counterpart (or vice versa) fails here.
"""

from collections.abc import Mapping
from dataclasses import fields, replace
from uuid import UUID

from synesthesia_machine.contracts import GraphSnapshotPayload, WireConnection, WireNode
from synesthesia_machine.graph import (
    ConnectionModel,
    GraphDocument,
    GraphSnapshot,
    GroupKind,
    NodeModel,
)

# Model fields that intentionally have no wire counterpart. The wire payload
# carries execution-relevant structure only; canvas organization (groups) is
# editor-only and never consumed by the engine.
_SNAPSHOT_FIELDS_EXCLUDED_FROM_WIRE = frozenset({"groups"})

# Deliberate model->wire renames (wire field names are wire-scoped).
_NODE_ALIASES = {"id": "node_id"}
_CONNECTION_ALIASES = {"id": "connection_id"}


def _wire_names(model_cls: type, aliases: Mapping[str, str]) -> frozenset[str]:
    return frozenset(aliases.get(field.name, field.name) for field in fields(model_cls))


def test_wire_node_fields_track_node_model_fields() -> None:
    assert {f.name for f in fields(WireNode)} == _wire_names(NodeModel, _NODE_ALIASES)


def test_wire_connection_fields_track_connection_model_fields() -> None:
    assert {f.name for f in fields(WireConnection)} == _wire_names(
        ConnectionModel, _CONNECTION_ALIASES
    )


def test_snapshot_payload_fields_track_snapshot_fields_except_excluded() -> None:
    expected = _wire_names(GraphSnapshot, {}) - _SNAPSHOT_FIELDS_EXCLUDED_FROM_WIRE
    assert {f.name for f in fields(GraphSnapshotPayload)} == expected


def test_payload_round_trip_preserves_every_wired_field() -> None:
    document = GraphDocument()
    source = document.add_node(
        "synmachine.utility.number",
        parameters={"number_type": "INT", "int_value": 3},
        position=(10.0, 20.0),
    )
    destination = document.add_node(
        "synmachine.utility.pass_through",
        position=(300.0, 20.0),
    )
    document.add_connection(source, "value", destination, "value")
    document.set_document_setting("background", "#101010")
    snapshot = document.snapshot()

    restored = GraphSnapshot.from_payload(snapshot.to_payload())

    assert restored == snapshot


def test_groups_never_cross_the_process_seam() -> None:
    document = GraphDocument()
    document.add_group(
        GroupKind.GROUP,
        title="scene",
        group_id=UUID("00000000-0000-0000-0000-000000000100"),
    )
    snapshot = document.snapshot()
    assert len(snapshot.groups) == 1

    restored = GraphSnapshot.from_payload(snapshot.to_payload())

    # The payload carries no groups; the reconstruction has none.
    assert restored == replace(snapshot, groups=())
