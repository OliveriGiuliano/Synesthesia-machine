"""Environment and dependency evidence acceptance test."""

import json
from pathlib import Path

from tools.reporting import (
    PHASE_ZERO_DISTRIBUTIONS,
    collect_environment_report,
    write_report,
)


def test_environment_report_records_runtime_and_dependencies(tmp_path: Path) -> None:
    output = tmp_path / "environment.json"
    report = collect_environment_report()
    write_report(report, output)
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report.python_version.startswith("3.12.")
    assert report.machine.upper() in {"AMD64", "X86_64"}
    assert report.memory_total_bytes > 0
    assert report.process_rss_bytes > 0
    assert set(persisted["dependencies"]) == set(PHASE_ZERO_DISTRIBUTIONS)
