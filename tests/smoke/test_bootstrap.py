"""Smoke checks for import-safe application bootstrap."""

from synesthesia_machine import __version__
from synesthesia_machine.app.bootstrap import main


def test_application_bootstrap_imports_without_starting_qt() -> None:
    assert __version__ == "0.1.0"
    assert callable(main)
