"""Qt-free recovery persistence and discovery behavior."""

import os
from pathlib import Path

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence.autosave import AutosaveStore


def test_autosave_store_discovers_valid_records_and_discards_them(tmp_path: Path) -> None:
    store = AutosaveStore(tmp_path / "recovery")
    document = GraphDocument()
    document.add_node("synmachine.utility.number")

    recovery_path = store.save(document.snapshot())
    (store.recovery_directory / "not-a-uuid.recovery.synmachine.json").write_text(
        "{}", encoding="utf-8"
    )

    records = store.discover()
    assert len(records) == 1
    assert records[0].document_id == document.document_id
    assert records[0].path == recovery_path
    assert records[0].is_newer_than(None)

    store.discard(document.document_id)
    assert not recovery_path.exists()


def test_recovery_record_compares_against_explicit_save_time(tmp_path: Path) -> None:
    store = AutosaveStore(tmp_path / "recovery")
    document = GraphDocument()
    recovery_path = store.save(document.snapshot())
    explicit_path = tmp_path / "graph.synmachine.json"
    explicit_path.write_text("explicit", encoding="utf-8")
    record = store.discover()[0]

    older = record.modified_time_ns - 1_000_000_000
    os.utime(explicit_path, ns=(older, older))
    assert record.is_newer_than(explicit_path)

    newer = recovery_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(explicit_path, ns=(newer, newer))
    assert not record.is_newer_than(explicit_path)
