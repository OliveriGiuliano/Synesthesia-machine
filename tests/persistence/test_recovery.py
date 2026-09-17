"""Recovery manifests, store-owned path resolution, and forced-crash verification."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from tools.recovery_probe import FORCED_CRASH_EXIT_CODE, run_forced_crash

from synesthesia_machine.graph import GraphDocument, GraphSnapshot, NodeModel
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import GraphPersistenceError, load_graph, save_graph
from synesthesia_machine.persistence.autosave import AutosaveStore
from synesthesia_machine.persistence.graph_io import graph_document_id


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


def test_explicit_path_for_keeps_manifest_path_without_scanning_recent_files(
    tmp_path: Path,
) -> None:
    document = GraphDocument()
    explicit = (tmp_path / "project" / "graph.synmachine.json").resolve()
    store = AutosaveStore(tmp_path / "recovery")
    store.save(document.snapshot(), explicit_path=explicit)

    record = store.discover()[0]
    assert store.explicit_path_for(record, []) == explicit
    # Unreadable recent files are never consulted for manifest-owned records.
    assert store.explicit_path_for(record, [tmp_path / "missing.synmachine.json"]) == explicit


def test_explicit_path_for_matches_pre_manifest_records_by_document_id(tmp_path: Path) -> None:
    document = GraphDocument()
    original = tmp_path / "graph.synmachine.json"
    save_graph(original, document.snapshot())
    store = AutosaveStore(tmp_path / "recovery")
    store.save(document.snapshot(), explicit_path=None)
    store.metadata_path_for(document.document_id).unlink()

    record = store.discover()[0]
    assert record.explicit_path is None
    assert store.explicit_path_for(record, [original]) == original

    other = GraphDocument()
    other_path = tmp_path / "other.synmachine.json"
    save_graph(other_path, other.snapshot())
    assert store.explicit_path_for(record, [other_path]) is None
    assert store.explicit_path_for(record, [other_path, original]) == original


def test_explicit_path_for_matches_without_full_graph_validation(tmp_path: Path) -> None:
    # A recent file whose node type the registry rejects: full load_graph
    # fails, but the lightweight document-ID read still matches it.
    document_id = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    snapshot = GraphSnapshot(
        document_id=document_id,
        revision=0,
        nodes=(
            NodeModel(
                id=UUID(int=1),
                type_id="synmachine.unknown.not_a_real_node",
                implementation_version=1,
                position=(0.0, 0.0),
            ),
        ),
        connections=(),
        document_settings={},
        groups=(),
    )
    invalid = tmp_path / "invalid.synmachine.json"
    save_graph(invalid, snapshot, retain_backup=False)
    with pytest.raises(GraphPersistenceError):
        load_graph(invalid, create_utility_registry())

    store = AutosaveStore(tmp_path / "recovery")
    store.save(snapshot, explicit_path=None)
    store.metadata_path_for(document_id).unlink()
    record = store.discover()[0]

    assert graph_document_id(invalid) == document_id
    assert store.explicit_path_for(record, [invalid]) == invalid
