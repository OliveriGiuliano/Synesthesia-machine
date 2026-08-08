"""Public hardware and privacy-aware support bundle diagnostics."""

from synesthesia_machine.diagnostics.bundle import (
    DiagnosticBundleResult,
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
    "DiagnosticBundleResult",
    "HardwareSnapshot",
    "NvidiaDiagnosticsAdapter",
    "NvidiaSnapshot",
    "collect_hardware_snapshot",
    "create_diagnostic_bundle",
    "dependency_versions",
    "redact_sensitive_paths",
]
