"""Structured graph validation diagnostics shared across the engine boundary.

These values cross the engine process boundary inside ``EngineActivation``
(the UI compiles, the engine revalidates), so they are owned by the
framework-independent contracts package. The graph package re-exports them
for facade stability.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    node_id: UUID | None = None
    connection_id: UUID | None = None
    port_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        return not self.errors
