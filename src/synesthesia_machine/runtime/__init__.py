"""Public headless execution-plan facade."""

from typing import TYPE_CHECKING

from synesthesia_machine.runtime.execution_plan import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    ScalarConversion,
)
from synesthesia_machine.runtime.scheduler import Scheduler, TickResult

if TYPE_CHECKING:
    from synesthesia_machine.runtime.engine_facade import EngineFacade
    from synesthesia_machine.runtime.in_process_engine import (
        InProcessEngineClient,
        LatestFrameGraphWorker,
    )

__all__ = [
    "CompiledNode",
    "EngineFacade",
    "ExecutionPlan",
    "InProcessEngineClient",
    "InputBinding",
    "LatestFrameGraphWorker",
    "PortKey",
    "ScalarConversion",
    "Scheduler",
    "TickResult",
]


def __getattr__(name: str) -> object:
    """Load the compiler-dependent facade without creating an import cycle."""

    if name == "EngineFacade":
        from synesthesia_machine.runtime.engine_facade import EngineFacade

        return EngineFacade
    if name in {"InProcessEngineClient", "LatestFrameGraphWorker"}:
        from synesthesia_machine.runtime.in_process_engine import (
            InProcessEngineClient,
            LatestFrameGraphWorker,
        )

        return {
            "InProcessEngineClient": InProcessEngineClient,
            "LatestFrameGraphWorker": LatestFrameGraphWorker,
        }[name]
    raise AttributeError(name)
