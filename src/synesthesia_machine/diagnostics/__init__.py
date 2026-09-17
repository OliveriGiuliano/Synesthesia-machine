"""Public hardware and privacy-aware support bundle diagnostics."""

from synesthesia_machine.diagnostics.bundle import (
    ARTIFACT_REDACTION,
    LOG_REDACTION,
    DiagnosticBundleResult,
    RedactionStrategy,
    create_diagnostic_bundle,
    dependency_versions,
    redact_sensitive_paths,
)
from synesthesia_machine.diagnostics.hardware import (
    HardwareSnapshot,
    NvidiaDiagnosticsAdapter,
    NvidiaSnapshot,
    collect_hardware_snapshot,
)

__all__ = [
    "ARTIFACT_REDACTION",
    "LOG_REDACTION",
    "DiagnosticBundleResult",
    "HardwareSnapshot",
    "NvidiaDiagnosticsAdapter",
    "NvidiaSnapshot",
    "RedactionStrategy",
    "collect_hardware_snapshot",
    "create_diagnostic_bundle",
    "dependency_versions",
    "redact_sensitive_paths",
]
