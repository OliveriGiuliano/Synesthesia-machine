"""Adapters between graph models and process-safe engine payloads."""

from synesthesia_machine.contracts.engine_messages import (
    GraphSnapshotPayload,
    WireConnection,
    WireNode,
)
from synesthesia_machine.graph.model import ConnectionModel, GraphSnapshot, NodeModel


def snapshot_to_payload(snapshot: GraphSnapshot) -> GraphSnapshotPayload:
    """Convert a graph snapshot into its process transport representation."""
    return GraphSnapshotPayload(
        document_id=snapshot.document_id,
        revision=snapshot.revision,
        nodes=tuple(
            WireNode(
                node.id,
                node.type_id,
                node.implementation_version,
                tuple(sorted(node.parameters.items())),
                node.position,
                node.size,
                node.user_label,
                node.collapsed,
                tuple(sorted(node.ui_state.items())),
            )
            for node in snapshot.nodes
        ),
        connections=tuple(
            WireConnection(
                connection.id,
                connection.source_node_id,
                connection.source_port_id,
                connection.destination_node_id,
                connection.destination_port_id,
            )
            for connection in snapshot.connections
        ),
        document_settings=tuple(sorted(snapshot.document_settings.items())),
    )


def payload_to_snapshot(payload: GraphSnapshotPayload) -> GraphSnapshot:
    """Convert a process transport payload into a graph snapshot."""
    return GraphSnapshot(
        document_id=payload.document_id,
        revision=payload.revision,
        nodes=tuple(
            NodeModel(
                id=node.node_id,
                type_id=node.type_id,
                implementation_version=node.implementation_version,
                parameters=dict(node.parameters),
                position=node.position,
                size=node.size,
                user_label=node.user_label,
                collapsed=node.collapsed,
                ui_state=dict(node.ui_state),
            )
            for node in payload.nodes
        ),
        connections=tuple(
            ConnectionModel(
                id=connection.connection_id,
                source_node_id=connection.source_node_id,
                source_port_id=connection.source_port_id,
                destination_node_id=connection.destination_node_id,
                destination_port_id=connection.destination_port_id,
            )
            for connection in payload.connections
        ),
        document_settings=dict(payload.document_settings),
    )


__all__ = ["payload_to_snapshot", "snapshot_to_payload"]
