"""Versioned dataclasses used by the Phase 0 spawn/IPC proof."""

from dataclasses import dataclass

ENGINE_PROTOCOL_VERSION = 1


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
    protocol_version: int = ENGINE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class ShutdownAcknowledged:
    request_id: str
    protocol_version: int = ENGINE_PROTOCOL_VERSION


EngineCommand = Ping | WriteSharedFrame | Shutdown
EngineEvent = Pong | SharedFrameReady | ShutdownAcknowledged
