"""Shared evidence-report infrastructure for the benchmark, soak, and profile tools.

The evidence conventions live in one module so a tool that produces evidence
imports a few names instead of re-rolling them:

* :func:`git_state` — the single git-state probe (commit + working-tree dirty),
  replacing each tool's own subprocess ``git`` shim.
* :func:`write_report` — the JSON writer (dataclass → indented JSON, newline
  normalized, parent directories created).
* :class:`EnvironmentReport` / :func:`collect_environment_report` — the
  tool-oriented environment summary, a projection of the app's
  ``HardwareSnapshot`` (``diagnostics.hardware``). The hardware and process
  facts have one owner; the dependency set is the only tool-side detail layered
  on top.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from synesthesia_machine.diagnostics import collect_hardware_snapshot

# Distributions the tool-oriented environment summary records versions of:
# the runtime libraries plus the dev tooling, matching the historical
# EnvironmentReport contract that the smoke test pins.
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
    """Tool-oriented projection of the app's ``HardwareSnapshot``."""

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


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in PHASE_ZERO_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not installed"
    return versions


def collect_environment_report() -> EnvironmentReport:
    """Project the app's ``HardwareSnapshot`` into the tool-oriented summary.

    Hardware and process facts come from ``diagnostics.hardware`` (the single
    owner); only the dependency set is a tool concern layered on top.
    """
    snapshot = collect_hardware_snapshot()
    return EnvironmentReport(
        generated_at_utc=snapshot.generated_at_utc,
        python_version=snapshot.python_version,
        python_executable=snapshot.python_executable,
        platform=snapshot.platform,
        machine=snapshot.machine,
        cpu_physical=snapshot.cpu_physical,
        cpu_logical=snapshot.cpu_logical,
        memory_total_bytes=snapshot.memory_total_bytes,
        memory_available_bytes=snapshot.memory_available_bytes,
        process_id=snapshot.process_id,
        process_rss_bytes=snapshot.process_rss_bytes,
        dependencies=_dependency_versions(),
    )


def git_state() -> tuple[str | None, bool | None]:
    """Return ``(commit, working_tree_dirty)`` for the current repository.

    ``commit`` is ``None`` outside a git worktree; ``dirty`` is ``None`` when
    the working-tree state could not be read. Both probes are non-throwing so
    a tool can report evidence from any checkout state.
    """
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit_value = commit.stdout.strip() if commit.returncode == 0 else None
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    return commit_value, dirty


def write_report(report: object, output_path: Path) -> str:
    """Serialize a dataclass report to ``output_path``; return the JSON payload.

    The payload is indented JSON plus a trailing newline, written with a
    normalized newline so committed evidence is byte-stable across platforms.
    """
    path = output_path.expanduser().resolve()
    payload = json.dumps(asdict(report), indent=2) + "\n"  # type: ignore[arg-type]
    path.write_text(payload, encoding="utf-8", newline="\n")
    return payload


__all__ = [
    "PHASE_ZERO_DISTRIBUTIONS",
    "EnvironmentReport",
    "collect_environment_report",
    "git_state",
    "write_report",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("packaging/environment-report.json"))
    args = parser.parse_args()
    write_report(collect_environment_report(), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
