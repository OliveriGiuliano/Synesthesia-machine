"""Stable cross-process and cross-package contracts exposed by Phase 0."""

from synesthesia_machine.contracts.engine_messages import (
    ENGINE_PROTOCOL_VERSION,
    Ping,
    Pong,
    SharedFrameReady,
    Shutdown,
    ShutdownAcknowledged,
    WriteSharedFrame,
)

__all__ = [
    "ENGINE_PROTOCOL_VERSION",
    "Ping",
    "Pong",
    "SharedFrameReady",
    "Shutdown",
    "ShutdownAcknowledged",
    "WriteSharedFrame",
]
