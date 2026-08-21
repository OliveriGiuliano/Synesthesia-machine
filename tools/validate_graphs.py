"""Validate graph files and report graph-schema and node-version migrations."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.persistence import (
    GRAPH_SCHEMA_VERSION,
    GraphPersistenceError,
    graph_from_data,
)


@dataclass(frozen=True, slots=True)
class GraphValidationResult:
    path: str
    valid: bool
    source_schema_version: int | None
    current_schema_version: int
    graph_migration_steps: int
    node_migration_steps: int
    node_count: int
    error: str | None


@dataclass(frozen=True, slots=True)
class GraphValidationReport:
    checked_files: int
    valid_files: int
    invalid_files: int
    migrated_files: int
    results: tuple[GraphValidationResult, ...]


def validate_graph(path: Path, registry: NodeRegistry) -> GraphValidationResult:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Graph root must be an object")
        data = cast("dict[str, object]", value)
        raw_schema = data.get("schema_version", 0)
        source_schema = (
            raw_schema if isinstance(raw_schema, int) and not isinstance(raw_schema, bool) else None
        )
        raw_nodes = data.get("nodes", [])
        node_steps = _node_migration_steps(raw_nodes, registry)
        snapshot = graph_from_data(data, registry)
    except (
        GraphPersistenceError,
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        return GraphValidationResult(
            str(path.resolve()),
            False,
            None,
            GRAPH_SCHEMA_VERSION,
            0,
            0,
            0,
            str(error),
        )
    graph_steps = max(0, GRAPH_SCHEMA_VERSION - source_schema) if source_schema is not None else 0
    return GraphValidationResult(
        str(path.resolve()),
        True,
        source_schema,
        GRAPH_SCHEMA_VERSION,
        graph_steps,
        node_steps,
        len(snapshot.nodes),
        None,
    )


def validate_paths(paths: tuple[Path, ...]) -> GraphValidationReport:
    registry = create_application_registry()
    files: set[Path] = set()
    for path in paths:
        if path.is_dir():
            files.update(path.rglob("*.synmachine.json"))
        elif path.is_file():
            files.add(path)
    results = tuple(validate_graph(path, registry) for path in sorted(files))
    valid_files = sum(result.valid for result in results)
    migrated_files = sum(
        result.valid and (result.graph_migration_steps > 0 or result.node_migration_steps > 0)
        for result in results
    )
    return GraphValidationReport(
        len(results),
        valid_files,
        len(results) - valid_files,
        migrated_files,
        results,
    )


def _node_migration_steps(raw_nodes: object, registry: NodeRegistry) -> int:
    if not isinstance(raw_nodes, list):
        return 0
    steps = 0
    for raw_node in cast("list[object]", raw_nodes):
        if not isinstance(raw_node, dict):
            continue
        node = cast("dict[object, object]", raw_node)
        type_id = node.get("type_id")
        version = node.get("implementation_version", node.get("version"))
        if (
            not isinstance(type_id, str)
            or isinstance(version, bool)
            or not isinstance(version, int)
        ):
            continue
        definition = registry.get(type_id)
        if definition is not None and version < definition.implementation_version:
            steps += definition.implementation_version - version
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("examples")])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_paths(tuple(args.paths))
    payload = json.dumps(asdict(report), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.invalid_files == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
