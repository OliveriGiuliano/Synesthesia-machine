"""Stable development command entry points executed through uv."""

import subprocess
import sys
from collections.abc import Sequence


def _run(arguments: Sequence[str]) -> None:
    subprocess.run([sys.executable, *arguments], check=True)


def test() -> int:
    """Run the complete automated test suite."""

    _run(["-m", "pytest"])
    return 0


def check() -> int:
    """Run formatting, linting, and strict static type checks."""

    _run(["-m", "ruff", "format", "--check", "."])
    _run(["-m", "ruff", "check", "."])
    _run(["-m", "pyright"])
    return 0
