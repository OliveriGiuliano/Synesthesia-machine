"""Architecture boundary tests for Phase 1 headless packages."""

from pathlib import Path


def test_headless_core_has_no_qt_imports() -> None:
    root = Path("src/synesthesia_machine")
    for package in ("contracts", "graph", "nodes", "runtime", "persistence"):
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "PySide6" not in text, path
            assert "synesthesia_machine.app" not in text, path
