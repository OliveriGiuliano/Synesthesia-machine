"""Force an editor-style process exit and verify autosave recovery survives it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import load_graph, save_graph
from synesthesia_machine.persistence.autosave import AutosaveStore

FORCED_CRASH_EXIT_CODE = 73


@dataclass(frozen=True, slots=True)
class ForcedCrashReport:
    child_exit_code: int
    document_id: str
    explicit_node_count: int
    recovered_node_count: int
    explicit_path_preserved: bool
    recovery_is_newer: bool
    unsaved_node_restored: bool


def run_forced_crash(workspace: Path) -> ForcedCrashReport:
    """Run the crash child and validate the recovery payload from a fresh process."""

    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    recovery_directory = workspace / "recovery"
    explicit_path = workspace / "explicit.synmachine.json"
    document = GraphDocument()
    document.add_node("synmachine.utility.number", position=(10.0, 20.0))
    save_graph(explicit_path, document.snapshot())
    unsaved_node_id = uuid4()
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--recovery-directory",
        str(recovery_directory),
        "--explicit-path",
        str(explicit_path),
        "--unsaved-node-id",
        str(unsaved_node_id),
    ]
    child = subprocess.run(command, check=False)

    store = AutosaveStore(recovery_directory)
    records = store.discover()
    if len(records) != 1:
        msg = f"Expected one recovery record, found {len(records)}"
        raise RuntimeError(msg)
    record = records[0]
    registry = create_application_registry()
    explicit = load_graph(explicit_path, registry)
    recovered = load_graph(record.path, registry)
    report = ForcedCrashReport(
        child_exit_code=child.returncode,
        document_id=str(recovered.document_id),
        explicit_node_count=len(explicit.nodes),
        recovered_node_count=len(recovered.nodes),
        explicit_path_preserved=record.explicit_path == explicit_path,
        recovery_is_newer=record.is_newer_than(explicit_path),
        unsaved_node_restored=any(node.id == unsaved_node_id for node in recovered.nodes),
    )
    if report.child_exit_code != FORCED_CRASH_EXIT_CODE:
        msg = f"Crash child returned {report.child_exit_code}, expected {FORCED_CRASH_EXIT_CODE}"
        raise RuntimeError(msg)
    if not report.explicit_path_preserved:
        raise RuntimeError("Recovery manifest did not preserve the explicit graph path")
    if not report.recovery_is_newer:
        raise RuntimeError("Recovery payload is not newer than the explicit graph")
    if not report.unsaved_node_restored:
        raise RuntimeError("Recovery payload did not contain the unsaved node")
    return report


def _run_crash_child(
    recovery_directory: Path,
    explicit_path: Path,
    unsaved_node_id: UUID,
) -> None:
    registry = create_application_registry()
    document = GraphDocument.from_snapshot(load_graph(explicit_path, registry))
    document.add_node(
        "synmachine.utility.number",
        node_id=unsaved_node_id,
        position=(320.0, 160.0),
    )
    AutosaveStore(recovery_directory).save(
        document.snapshot(),
        explicit_path=explicit_path,
    )
    os._exit(FORCED_CRASH_EXIT_CODE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("build/recovery-probe"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--explicit-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--unsaved-node-id", type=UUID, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        if (
            args.recovery_directory is None
            or args.explicit_path is None
            or args.unsaved_node_id is None
        ):
            parser.error("Crash child arguments are required")
        _run_crash_child(
            args.recovery_directory,
            args.explicit_path,
            args.unsaved_node_id,
        )
        return 0

    report = run_forced_crash(args.workspace)
    payload = json.dumps(asdict(report), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
