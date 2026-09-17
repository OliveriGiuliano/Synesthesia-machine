"""Architecture boundary tests for package import direction.

The rules encoded here mirror AGENTS.md:

* Every package except the UI owners (``app``, ``ui``) is headless: it may
  not import Qt, the UI layer, or the application composition root. The
  headless set is derived from the package directories, so a newly added
  package is enforced by default instead of needing a tuple edit.
* ``contracts`` is the lowest layer: it must not import any sibling
  implementation package. A single documented exception exists (see
  ``contracts/README.md``): ``engine_client`` names the graph
  ``GraphSnapshot`` type under ``TYPE_CHECKING`` so the client protocol can
  reference the snapshot it accepts without creating a runtime edge.
* ``graph`` may not depend on persistence (authoring model versus saved
  JSON conversion are separate layers).

Imports are collected from every ``ast`` import node (top-level, nested, and
in-function lazy imports alike), including relative imports resolved to
their absolute module, so no import style can hide behind the guard.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path("src") / "synesthesia_machine"

#: The only packages that may import Qt or the UI layer. Everything else
#: discovered under ``ROOT`` is headless and enforced below.
UI_OWNERS = frozenset({"app", "ui"})

#: Documented directional rules beyond the headless rule, keyed by package:
#: sibling packages that the key package must never import.
FORBIDDEN_SIBLINGS = {
    "contracts": frozenset(
        {
            "app",
            "diagnostics",
            "graph",
            "media",
            "midi",
            "nodes",
            "persistence",
            "runtime",
            "ui",
        }
    ),
    "graph": frozenset({"persistence"}),
}

#: Type-only (``TYPE_CHECKING``) imports that are permitted despite the
#: rules above. Kept as a deliberately tiny, documented exception list.
TYPE_ONLY_EXCEPTIONS = {
    "contracts": frozenset({"synesthesia_machine.graph.model"}),
}


def _package_dirs() -> dict[str, Path]:
    return {
        path.name: path
        for path in sorted(ROOT.iterdir())
        if path.is_dir() and (path / "__init__.py").is_file()
    }


def _import_nodes(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _type_only_node_ids(tree: ast.Module) -> frozenset[int]:
    """Import nodes that live inside a module-level ``if TYPE_CHECKING:``."""

    ids: set[int] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.If):
            continue
        names = {name.id for name in ast.walk(statement.test) if isinstance(name, ast.Name)}
        if "TYPE_CHECKING" not in names:
            continue
        for node in ast.walk(statement):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                ids.add(id(node))
    return frozenset(ids)


def _absolute_module(file_package: str, node: ast.ImportFrom | ast.Import) -> str | None:
    """Resolve an import node to an absolute module (relative-aware)."""

    if isinstance(node, ast.Import):
        return node.names[0].name
    if not node.module:
        return None
    if node.level == 0:
        return node.module
    parts = file_package.split(".")
    up = node.level - 1
    if up > len(parts):
        return node.module  # malformed; treat as opaque
    base = ".".join(parts[: len(parts) - up])
    return f"{base}.{node.module}" if base else node.module


def _imports(package: Path) -> Iterator[tuple[str, str, bool]]:
    """Yield ``(absolute_module, source_file, is_type_only)`` per import."""

    for file in sorted(package.rglob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        type_only = _type_only_node_ids(tree)
        file_package = ".".join(file.relative_to(ROOT.parent).with_suffix("").parts)
        for node in _import_nodes(tree):
            module = _absolute_module(file_package, node)
            if module is None:
                continue
            yield module, str(file.relative_to(ROOT)), id(node) in type_only


def test_discovered_packages_look_sane() -> None:
    packages = _package_dirs()
    # Anchor the discovery so a broken ROOT cannot silently pass with no
    # packages; the UI owners must exist for the headless rule to mean
    # anything.
    assert packages
    assert packages.keys() >= UI_OWNERS, packages


def test_headless_packages_stay_headless() -> None:
    violations: list[str] = []
    for name, path in _package_dirs().items():
        if name in UI_OWNERS:
            continue
        for module, file, _ in _imports(path):
            if (
                module.startswith("PySide6")
                or module.startswith("synesthesia_machine.app")
                or module.startswith("synesthesia_machine.ui")
            ):
                violations.append(f"{name}/{file}: {module}")
    assert not violations, "headless package imported UI/Qt code:\n" + "\n".join(violations)


def test_forbidden_package_import_directions() -> None:
    violations: list[str] = []
    for name, path in _package_dirs().items():
        forbidden = FORBIDDEN_SIBLINGS.get(name, frozenset())
        exceptions = TYPE_ONLY_EXCEPTIONS.get(name, frozenset())
        for module, file, type_only in _imports(path):
            if not module.startswith("synesthesia_machine."):
                continue
            target = module.split(".", 2)[1].split(".")[0]
            if target not in forbidden or target == name:
                continue
            if type_only and module in exceptions:
                continue
            context = "type-only import " if type_only else ""
            violations.append(f"{name}/{file}: {context}imports {module}")
    assert not violations, "forbidden import direction:\n" + "\n".join(violations)


def test_external_node_imports_use_facades() -> None:
    """Packages outside ``nodes`` may only import the package or subpackage facades.

    Importing ``nodes.base`` / ``nodes.registry`` (or any other internal module)
    freezes those module paths as public API and breaks future reorganizations
    (master architecture 17.2).
    """

    subpackages = {
        path.name
        for path in (ROOT / "nodes").iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    allowed = {"synesthesia_machine.nodes"} | {
        f"synesthesia_machine.nodes.{name}" for name in subpackages
    }
    violations: list[str] = []
    for name, path in _package_dirs().items():
        if name == "nodes":
            continue
        for module, file, _ in _imports(path):
            if not module.startswith("synesthesia_machine.nodes."):
                continue
            if module in allowed:
                continue
            violations.append(f"{name}/{file}: imports {module}")
    assert not violations, (
        "node imports outside the package must go through the facade:\n" + "\n".join(violations)
    )
