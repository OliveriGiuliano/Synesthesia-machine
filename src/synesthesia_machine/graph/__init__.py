"""Public Qt-free graph-domain facade."""

from synesthesia_machine.graph.compiler import (
    CompilationResult,
    ConnectionCompatibility,
    GraphCompiler,
    types_compatible,
)
from synesthesia_machine.graph.layout import (
    AlignMode,
    DistributionAxis,
    LayoutBox,
    align_boxes,
    distribute_boxes,
    tidy_boxes,
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
    "AlignMode",
    "CompilationResult",
    "ConnectionCompatibility",
    "ConnectionModel",
    "DistributionAxis",
    "GraphCompiler",
    "GraphDocument",
    "GraphSnapshot",
    "GroupKind",
    "GroupModel",
    "LayoutBox",
    "LiteralValue",
    "NodeModel",
    "ValidationIssue",
    "ValidationReport",
    "ValidationSeverity",
    "align_boxes",
    "distribute_boxes",
    "tidy_boxes",
    "types_compatible",
]
