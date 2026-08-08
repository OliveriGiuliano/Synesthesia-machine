"""Public Qt-free graph-domain facade."""

from synesthesia_machine.graph.compiler import (
    CompilationResult,
    ConnectionCompatibility,
    GraphCompiler,
    types_compatible,
)
from synesthesia_machine.graph.model import (
    ConnectionModel,
    GraphDocument,
    GraphSnapshot,
    GroupKind,
    GroupModel,
    LiteralValue,
    NodeModel,
)
from synesthesia_machine.graph.validation import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)

__all__ = [
    "CompilationResult",
    "ConnectionCompatibility",
    "ConnectionModel",
    "GraphCompiler",
    "GraphDocument",
    "GraphSnapshot",
    "GroupKind",
    "GroupModel",
    "LiteralValue",
    "NodeModel",
    "ValidationIssue",
    "ValidationReport",
    "ValidationSeverity",
    "types_compatible",
]
