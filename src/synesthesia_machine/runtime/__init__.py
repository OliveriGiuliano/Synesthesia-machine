"""Public headless execution-plan facade."""

from typing import TYPE_CHECKING

from synesthesia_machine.runtime.engine_session import EngineSession, RestartOutcome
from synesthesia_machine.runtime.execution_plan import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    ScalarConversion,
)
from synesthesia_machine.runtime.profiling import RuntimeProfiler
from synesthesia_machine.runtime.scheduler import Scheduler, TickResult

if TYPE_CHECKING:
    from synesthesia_machine.runtime.engine_client import EngineProtocolError, ProcessEngineClient
    from synesthesia_machine.runtime.engine_facade import EngineFacade
    from synesthesia_machine.runtime.in_process_engine import (
        InProcessEngineClient,
        LatestFrameGraphWorker,
        TickObserver,
    )
    from synesthesia_machine.runtime.midi_export import (
        MidiExportError,
        MidiExportProgress,
        MidiExportResult,
        run_midi_export,
    )
    from synesthesia_machine.runtime.preview_channel import (
        AttachedPreviewSlot,
        InMemoryPreviewTransport,
        OwnedPreviewSlot,
        PreviewTransport,
        SharedMemoryPreviewReader,
        SharedMemoryPreviewWriter,
    )
    from synesthesia_machine.runtime.previews import PreviewBroker

__all__ = [
    "AttachedPreviewSlot",
    "CompiledNode",
    "EngineFacade",
    "EngineProtocolError",
    "EngineSession",
    "ExecutionPlan",
    "InMemoryPreviewTransport",
    "InProcessEngineClient",
    "InputBinding",
    "LatestFrameGraphWorker",
    "MidiExportError",
    "MidiExportProgress",
    "MidiExportResult",
    "OwnedPreviewSlot",
    "PortKey",
    "PreviewBroker",
    "PreviewTransport",
    "ProcessEngineClient",
    "RestartOutcome",
    "RuntimeProfiler",
    "ScalarConversion",
    "Scheduler",
    "SharedMemoryPreviewReader",
    "SharedMemoryPreviewWriter",
    "TickObserver",
    "TickResult",
    "run_midi_export",
]


def __getattr__(name: str) -> object:
    """Load the compiler-dependent facade without creating an import cycle."""

    if name == "EngineFacade":
        from synesthesia_machine.runtime.engine_facade import EngineFacade

        return EngineFacade
    if name in {"EngineProtocolError", "ProcessEngineClient"}:
        from synesthesia_machine.runtime.engine_client import (
            EngineProtocolError,
            ProcessEngineClient,
        )

        return {
            "EngineProtocolError": EngineProtocolError,
            "ProcessEngineClient": ProcessEngineClient,
        }[name]
    if name in {"InProcessEngineClient", "LatestFrameGraphWorker", "TickObserver"}:
        from synesthesia_machine.runtime.in_process_engine import (
            InProcessEngineClient,
            LatestFrameGraphWorker,
            TickObserver,
        )

        return {
            "InProcessEngineClient": InProcessEngineClient,
            "LatestFrameGraphWorker": LatestFrameGraphWorker,
            "TickObserver": TickObserver,
        }[name]
    if name in {"MidiExportError", "MidiExportProgress", "MidiExportResult", "run_midi_export"}:
        from synesthesia_machine.runtime.midi_export import (
            MidiExportError,
            MidiExportProgress,
            MidiExportResult,
            run_midi_export,
        )

        return {
            "MidiExportError": MidiExportError,
            "MidiExportProgress": MidiExportProgress,
            "MidiExportResult": MidiExportResult,
            "run_midi_export": run_midi_export,
        }[name]
    if name == "PreviewBroker":
        from synesthesia_machine.runtime.previews import PreviewBroker

        return PreviewBroker
    if name in {
        "AttachedPreviewSlot",
        "InMemoryPreviewTransport",
        "OwnedPreviewSlot",
        "PreviewTransport",
        "SharedMemoryPreviewReader",
        "SharedMemoryPreviewWriter",
    }:
        from synesthesia_machine.runtime.preview_channel import (
            AttachedPreviewSlot,
            InMemoryPreviewTransport,
            OwnedPreviewSlot,
            PreviewTransport,
            SharedMemoryPreviewReader,
            SharedMemoryPreviewWriter,
        )

        return {
            "AttachedPreviewSlot": AttachedPreviewSlot,
            "InMemoryPreviewTransport": InMemoryPreviewTransport,
            "OwnedPreviewSlot": OwnedPreviewSlot,
            "PreviewTransport": PreviewTransport,
            "SharedMemoryPreviewReader": SharedMemoryPreviewReader,
            "SharedMemoryPreviewWriter": SharedMemoryPreviewWriter,
        }[name]
    raise AttributeError(name)
