"""Recovery manifests and forced-crash verification."""

from __future__ import annotations

from pathlib import Path

from tools.recovery_probe import FORCED_CRASH_EXIT_CODE, run_forced_crash

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence.autosave import AutosaveStore


def test_recovery_manifest_preserves_explicit_path_without_recent_files(tmp_path: Path) -> None:
    explicit_path = (tmp_path / "project" / "graph.synmachine.json").resolve()
    document = GraphDocument()
    store = AutosaveStore(tmp_path / "recovery")

    recovery_path = store.save(document.snapshot(), explicit_path=explicit_path)
    records = store.discover()

    assert len(records) == 1
    assert records[0].path == recovery_path
    assert records[0].explicit_path == explicit_path
    assert store.metadata_path_for(document.document_id).is_file()

    store.discard(document.document_id)
    assert not recovery_path.exists()
    assert not store.metadata_path_for(document.document_id).exists()


def test_corrupt_manifest_does_not_hide_valid_recovery_payload(tmp_path: Path) -> None:
    document = GraphDocument()
    store = AutosaveStore(tmp_path / "recovery")
    store.save(document.snapshot(), explicit_path=tmp_path / "graph.synmachine.json")
    store.metadata_path_for(document.document_id).write_text("{broken", encoding="utf-8")

    records = store.discover()

    assert len(records) == 1
    assert records[0].document_id == document.document_id
    assert records[0].explicit_path is None


def test_forced_process_exit_restores_unsaved_graph_state(tmp_path: Path) -> None:
    report = run_forced_crash(tmp_path / "crash-harness")

    assert report.child_exit_code == FORCED_CRASH_EXIT_CODE
    assert report.explicit_node_count == 1
    assert report.recovered_node_count == 2
    assert report.explicit_path_preserved
    assert report.recovery_is_newer
    assert report.unsaved_node_restored
