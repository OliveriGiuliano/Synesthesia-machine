"""Guards for domain-oriented active repository ownership."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_ROOTS = (
    ROOT / "src",
    ROOT / "tests",
    ROOT / "tools",
    ROOT / "examples",
    ROOT / "benchmarks",
    ROOT / "packaging",
    ROOT / "docs/agent-playbooks",
    ROOT / "docs/architecture",
    ROOT / "docs/reference",
)
TEXT_SUFFIXES = {".md", ".py", ".ps1", ".sh", ".toml"}
PHASE_PATH = re.compile(r"^phase[_-]?\d", re.IGNORECASE)
STALE_ACTIVE_REFERENCE = re.compile(
    r"(?:tools\.phase\d|(?:tests|examples|docs)[/\\]phase[_-]?\d|synesthesia_machine_design)"
)


def _active_files() -> tuple[Path, ...]:
    return tuple(
        path
        for root in ACTIVE_ROOTS
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def test_active_paths_use_domain_ownership_instead_of_delivery_phases() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in _active_files()
        if any(PHASE_PATH.match(part) for part in path.relative_to(ROOT).parts)
    ]

    assert offenders == []


def test_active_text_does_not_route_to_retired_phase_paths_or_modules() -> None:
    offenders = []
    for path in _active_files():
        if path == Path(__file__).resolve():
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        if STALE_ACTIVE_REFERENCE.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []
