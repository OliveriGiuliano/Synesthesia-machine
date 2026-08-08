"""Hardware and process diagnostics with an optional NVIDIA adapter boundary."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import psutil


@dataclass(frozen=True, slots=True)
class NvidiaSnapshot:
    available: bool
    reason: str | None = None
    device_name: str | None = None
    driver_version: str | None = None
    vram_total_bytes: int | None = None
    vram_used_bytes: int | None = None
    utilization_percent: float | None = None


class NvidiaDiagnosticsAdapter(Protocol):
    def snapshot(self) -> NvidiaSnapshot: ...


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    generated_at_utc: str
    platform: str
    machine: str
    python_version: str
    python_executable: str
    cpu_name: str
    cpu_physical: int | None
    cpu_logical: int | None
    cpu_percent: float
    memory_total_bytes: int
    memory_available_bytes: int
    process_id: int
    process_cpu_percent: float
    process_rss_bytes: int
    nvidia: NvidiaSnapshot


def collect_hardware_snapshot(
    nvidia_adapter: NvidiaDiagnosticsAdapter | None = None,
) -> HardwareSnapshot:
    """Collect one non-blocking whole-system/process sample."""

    memory = psutil.virtual_memory()
    process = psutil.Process()
    nvidia = _collect_nvidia(nvidia_adapter)
    return HardwareSnapshot(
        generated_at_utc=datetime.now(UTC).isoformat(),
        platform=platform.platform(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        python_executable=sys.executable,
        cpu_name=platform.processor() or "unknown",
        cpu_physical=psutil.cpu_count(logical=False),
        cpu_logical=psutil.cpu_count(logical=True),
        cpu_percent=psutil.cpu_percent(interval=None),
        memory_total_bytes=memory.total,
        memory_available_bytes=memory.available,
        process_id=process.pid,
        process_cpu_percent=process.cpu_percent(interval=None),
        process_rss_bytes=process.memory_info().rss,
        nvidia=nvidia,
    )


def _collect_nvidia(adapter: NvidiaDiagnosticsAdapter | None) -> NvidiaSnapshot:
    if adapter is None:
        return NvidiaSnapshot(False, "NVIDIA diagnostics adapter is not installed")
    try:
        return adapter.snapshot()
    except Exception as error:  # Optional diagnostics must never block bundle export.
        return NvidiaSnapshot(False, f"NVIDIA diagnostics unavailable: {error}")


__all__ = [
    "HardwareSnapshot",
    "NvidiaDiagnosticsAdapter",
    "NvidiaSnapshot",
    "collect_hardware_snapshot",
]
