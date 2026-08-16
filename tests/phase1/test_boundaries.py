"""Architecture boundary tests for Phase 1 headless packages."""

import ast
from pathlib import Path


def test_headless_core_has_no_qt_imports() -> None:
    root = Path("src/synesthesia_machine")
    for package in ("contracts", "graph", "nodes", "runtime", "persistence"):
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "PySide6" not in text, path
            assert "synesthesia_machine.app" not in text, path


def test_contracts_do_not_import_implementation_packages_at_runtime() -> None:
    contracts = Path("src/synesthesia_machine/contracts")
    forbidden = (
        "synesthesia_machine.app",
        "synesthesia_machine.graph",
        "synesthesia_machine.nodes",
        "synesthesia_machine.persistence",
        "synesthesia_machine.runtime",
        "synesthesia_machine.ui",
    )

    for path in contracts.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for statement in tree.body:
            imported: tuple[str, ...] = ()
            if isinstance(statement, ast.Import):
                imported = tuple(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom) and statement.module is not None:
                imported = (statement.module,)
            for module in imported:
                assert not module.startswith(forbidden), (path, module)
