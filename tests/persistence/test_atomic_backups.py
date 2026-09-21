"""Atomic replacement, one-generation backup, and cleanup tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import graph_to_json, save_graph


def _document(value: float) -> GraphDocument:
    document = GraphDocument()
    document.add_node(
        "synmachine.utility.number",
        implementation_version=4,
        parameters={"value": value},
    )
    return document


def test_successive_saves_retain_exactly_one_previous_generation(tmp_path: Path) -> None:
    path = tmp_path / "graph.synmachine.json"
    backup = path.with_name(f"{path.name}.bak")
    first = _document(1.0)
    second = _document(2.0)
    third = _document(3.0)

    save_graph(path, first.snapshot())
    assert not backup.exists()
    first_text = path.read_text(encoding="utf-8")
    save_graph(path, second.snapshot())
    assert backup.read_text(encoding="utf-8") == first_text
    second_text = path.read_text(encoding="utf-8")
    save_graph(path, third.snapshot())

    assert path.read_text(encoding="utf-8") == graph_to_json(third.snapshot())
    assert backup.read_text(encoding="utf-8") == second_text
    assert not tuple(tmp_path.glob("*.bak.bak"))
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_failed_destination_replace_preserves_explicit_file_and_cleans_temporaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "graph.synmachine.json"
    original = _document(1.0)
    replacement = _document(2.0)
    save_graph(path, original.snapshot())
    original_text = path.read_text(encoding="utf-8")
    real_replace = os.replace

    def fail_destination_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == path:
            raise OSError("simulated atomic replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(
        "synesthesia_machine.persistence.graph_io.os.replace", fail_destination_replace
    )
    with pytest.raises(OSError, match="simulated"):
        save_graph(path, replacement.snapshot())

    assert path.read_text(encoding="utf-8") == original_text
    assert path.with_name(f"{path.name}.bak").read_text(encoding="utf-8") == original_text
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_backup_can_be_disabled_for_recovery_style_writes(tmp_path: Path) -> None:
    path = tmp_path / "recovery.synmachine.json"
    save_graph(path, _document(1.0).snapshot(), retain_backup=False)
    save_graph(path, _document(2.0).snapshot(), retain_backup=False)

    assert path.is_file()
    assert not path.with_name(f"{path.name}.bak").exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-only")
def test_save_fsyncs_containing_directory_for_durable_renames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "graph.synmachine.json"
    parent = str(tmp_path)
    opened: dict[int, str] = {}
    fsynced: list[int] = []
    real_open = os.open
    real_fsync = os.fsync

    def recording_open(file: str | bytes | Path, flags: int, mode: int = 0o777) -> int:
        descriptor = real_open(file, flags, mode)
        opened[descriptor] = os.fsdecode(file)
        return descriptor

    def recording_fsync(descriptor: int) -> None:
        fsynced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr("synesthesia_machine.persistence.graph_io.os.open", recording_open)
    monkeypatch.setattr("synesthesia_machine.persistence.graph_io.os.fsync", recording_fsync)

    # Two saves so both the backup replace and the final replace run.
    save_graph(path, _document(1.0).snapshot())
    save_graph(path, _document(2.0).snapshot())

    directory_fds = [descriptor for descriptor, name in opened.items() if name == parent]
    assert directory_fds, "expected the containing directory to be opened for a durability fsync"
    for descriptor in directory_fds:
        assert descriptor in fsynced, "each renamed directory entry must be fsynced"
