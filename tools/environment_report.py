"""Generate a Windows, hardware, and dependency environment report."""

import argparse
import importlib.metadata
import json
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import psutil

PHASE_ZERO_DISTRIBUTIONS = (
    "av",
    "mido",
    "numpy",
    "opencv-python",
    "psutil",
    "pyside6",
    "python-rtmidi",
    "sounddevice",
    "pyright",
    "pytest",
    "ruff",
)


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    generated_at_utc: str
    python_version: str
    python_executable: str
    platform: str
    machine: str
    cpu_physical: int | None
    cpu_logical: int | None
    memory_total_bytes: int
    memory_available_bytes: int
    process_id: int
    process_rss_bytes: int
    dependencies: dict[str, str]


def collect_environment_report() -> EnvironmentReport:
    memory = psutil.virtual_memory()
    process = psutil.Process()
    return EnvironmentReport(
        generated_at_utc=datetime.now(UTC).isoformat(),
        python_version=platform.python_version(),
        python_executable=sys.executable,
        platform=platform.platform(),
        machine=platform.machine(),
        cpu_physical=psutil.cpu_count(logical=False),
        cpu_logical=psutil.cpu_count(logical=True),
        memory_total_bytes=memory.total,
        memory_available_bytes=memory.available,
        process_id=process.pid,
        process_rss_bytes=process.memory_info().rss,
        dependencies={
            distribution: importlib.metadata.version(distribution)
            for distribution in PHASE_ZERO_DISTRIBUTIONS
        },
    )


def write_environment_report(output_path: Path) -> EnvironmentReport:
    report = collect_environment_report()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("packaging/environment-report.json"))
    args = parser.parse_args()
    report = write_environment_report(args.output)
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
