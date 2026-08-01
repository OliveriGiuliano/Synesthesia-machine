"""Trusted, versioned engine control and asynchronous event contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from synesthesia_machine.contracts.engine_client import (
    EngineActivation,
    EngineMetrics,
    NotePreview,
    SourceStatus,
)
from synesthesia_machine.contracts.runtime_values import ColorValue
from synesthesia_machine.nodes.base import ResetReason

if TYPE_CHECKING:
    from synesthesia_machine.graph.model import GraphSnapshot

ENGINE_PROTOCOL_VERSION = 4

type SnapshotLiteral = str | int | float | bool | ColorValue | None


@dataclass(frozen=True, slots=True)
class WireNode:
    node_id: UUID
    type_id: str
    implementation_version: int
    parameters: tuple[tuple[str, SnapshotLiteral], ...]
    position: tuple[float, float]
    size: tuple[float, float] | None
    user_label: str | None
    collapsed: bool
    ui_state: tuple[tuple[str, SnapshotLiteral], ...]


@dataclass(frozen=True, slots=True)
class WireConnection:
    connection_id: UUID
    source_node_id: UUID
    source_port_id: str
    destination_node_id: UUID
    destination_port_id: str


@dataclass(frozen=True, slots=True)
class GraphSnapshotPayload:
    document_id: UUID
    revision: int
    nodes: tuple[WireNode, ...]
    connections: tuple[WireConnection, ...]
    document_settings: tuple[tuple[str, SnapshotLiteral], ...]

    @classmethod
    def from_snapshot(cls, snapshot: GraphSnapshot) -> GraphSnapshotPayload:
        return cls(
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

    def to_snapshot(self) -> GraphSnapshot:
        from synesthesia_machine.graph.model import ConnectionModel, GraphSnapshot, NodeModel

        return GraphSnapshot(
            document_id=self.document_id,
            revision=self.revision,
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
                for node in self.nodes
            ),
            connections=tuple(
                ConnectionModel(
                    id=connection.connection_id,
                    source_node_id=connection.source_node_id,
                    source_port_id=connection.source_port_id,
                    destination_node_id=connection.destination_node_id,
                    destination_port_id=connection.destination_port_id,
                )
                for connection in self.connections
            ),
            document_settings=dict(self.document_settings),
        )


class TransportAction(StrEnum):
    PLAY = "PLAY"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"
    RELOAD = "RELOAD"
    SEEK = "SEEK"


@dataclass(frozen=True, slots=True)
class PreviewSlotDescriptor:
    node_id: UUID
    shared_memory_name: str
    generation: int
    width: int
    height: int
    channels: int


@dataclass(frozen=True, slots=True)
class Handshake:
    request_id: str
    parent_process_id: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class HandshakeAcknowledged:
    request_id: str
    child_process_id: int
    started_monotonic_ns: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class ActivateGraph:
    request_id: str
    graph_revision: int
    snapshot: GraphSnapshotPayload
    demand_roots: tuple[UUID, ...] | None = None
    reset_reason: ResetReason = ResetReason.PLAN_REPLACED
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class GraphActivationAcknowledged:
    request_id: str
    graph_revision: int
    activation: EngineActivation
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class TransportCommand:
    request_id: str
    action: TransportAction
    graph_revision: int | None
    source_node_id: UUID | None = None
    source_time_s: float | None = None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class Panic:
    request_id: str
    graph_revision: int | None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class QuerySourceStatus:
    request_id: str
    graph_revision: int | None
    source_node_id: UUID | None = None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class SourceStatusResponse:
    request_id: str
    graph_revision: int | None
    statuses: tuple[SourceStatus, ...]
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class QueryMetrics:
    request_id: str
    graph_revision: int | None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class MetricsResponse:
    request_id: str
    graph_revision: int | None
    metrics: EngineMetrics
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class WaitUntilIdle:
    request_id: str
    graph_revision: int | None
    timeout_s: float
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class IdleResponse:
    request_id: str
    graph_revision: int | None
    idle: bool
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class ConfigurePreviewSlot:
    request_id: str
    graph_revision: int
    descriptor: PreviewSlotDescriptor
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class PreviewSlotConfigured:
    request_id: str
    graph_revision: int
    node_id: UUID
    generation: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class CommandAcknowledged:
    request_id: str
    graph_revision: int | None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class CommandFailed:
    request_id: str
    graph_revision: int | None
    error_type: str
    message: str
    details: str | None = None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class ProtocolMismatch:
    request_id: str
    expected_version: int
    received_version: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class Heartbeat:
    sequence: int
    child_process_id: int
    sent_monotonic_ns: int
    started_monotonic_ns: int
    graph_revision: int | None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class PreviewFormatChanged:
    graph_revision: int
    node_id: UUID
    generation: int
    width: int
    height: int
    channels: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class NotePreviewsPublished:
    graph_revision: int
    previews: tuple[NotePreview, ...]
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class EngineErrorPublished:
    graph_revision: int | None
    message: str
    details: str | None = None
    fatal: bool = False
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class Ping:
    request_id: str
    sent_monotonic_ns: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class Pong:
    request_id: str
    child_process_id: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class WriteSharedFrame:
    request_id: str
    shared_memory_name: str
    width: int
    height: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class SharedFrameReady:
    request_id: str
    sequence: int
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class Shutdown:
    request_id: str
    graph_revision: int | None = None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class ShutdownAcknowledged:
    request_id: str
    graph_revision: int | None = None
    protocol_version: int = ENGINE_PROTOCOL_VERSION


EngineCommand = (
    Ping
    | WriteSharedFrame
    | Handshake
    | ActivateGraph
    | TransportCommand
    | Panic
    | QuerySourceStatus
    | QueryMetrics
    | WaitUntilIdle
    | ConfigurePreviewSlot
    | Shutdown
)
EngineResponse = (
    Pong
    | SharedFrameReady
    | HandshakeAcknowledged
    | GraphActivationAcknowledged
    | SourceStatusResponse
    | MetricsResponse
    | IdleResponse
    | PreviewSlotConfigured
    | CommandAcknowledged
    | CommandFailed
    | ProtocolMismatch
    | ShutdownAcknowledged
)
EngineAsyncEvent = Heartbeat | PreviewFormatChanged | NotePreviewsPublished | EngineErrorPublished
EngineEvent = EngineResponse | EngineAsyncEvent
