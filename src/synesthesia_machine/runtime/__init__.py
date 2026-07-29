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

__all__ = [
    "CompiledNode",
    "EngineFacade",
    "ExecutionPlan",
    "InputBinding",
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
    raise AttributeError(name)
