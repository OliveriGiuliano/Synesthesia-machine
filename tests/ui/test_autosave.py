"""Qt-free recovery persistence and discovery behavior."""

import os
import threading
import time
from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtWidgets import QApplication

from synesthesia_machine.graph import GraphDocument, GraphSnapshot
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence.autosave import AutosaveStore
from synesthesia_machine.ui.autosave_controller import AutosaveController
from synesthesia_machine.ui.session import DocumentSession


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


def test_autosave_controller_is_non_blocking_and_coalesces_pending_snapshots(
    qapp: QApplication, tmp_path: Path
) -> None:
    started = threading.Event()
    release = threading.Event()
    revisions: list[int] = []

    class _BlockingStore:
        def save(
            self,
            snapshot: GraphSnapshot,
            *,
            explicit_path: object = None,
        ) -> Path:
            del explicit_path
            revision = snapshot.revision
            revisions.append(revision)
            if len(revisions) == 1:
                started.set()
                assert release.wait(2.0)
            return tmp_path / f"{revision}.recovery.synmachine.json"

        def discard(self, document_id: object) -> None:
            del document_id

    controller = AutosaveController(cast(AutosaveStore, _BlockingStore()))
    saved: list[Path] = []
    controller.saved.connect(saved.append)
    document = GraphDocument()
    document.add_node("test.one")

    before = time.monotonic()
    controller.request(document.snapshot(), explicit_path=None)
    assert time.monotonic() - before < 0.25
    assert started.wait(1.0)

    document.add_node("test.two")
    controller.request(document.snapshot(), explicit_path=None)
    document.add_node("test.three")
    controller.request(document.snapshot(), explicit_path=None)
    release.set()

    deadline = time.monotonic() + 2.0
    while len(saved) < 2 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)

    assert revisions == [1, 3]
    assert [path.name for path in saved] == [
        "1.recovery.synmachine.json",
        "3.recovery.synmachine.json",
    ]
    controller.close()


def test_autosave_controller_discards_recovery_when_the_document_returns_to_clean(
    qapp: QApplication, tmp_path: Path
) -> None:
    store = AutosaveStore(tmp_path / "recovery")
    session = DocumentSession(create_utility_registry())
    controller = AutosaveController(store, session=session)
    saved: list[object] = []
    controller.saved.connect(saved.append)

    session.add_node("synmachine.utility.number", (0.0, 0.0))
    controller.request(session.document.snapshot(), explicit_path=None)

    deadline = time.monotonic() + 2.0
    while not saved and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert saved
    assert store.discover()

    # Undo back to the last saved state without an autosave running: the
    # stale recovery record must be discarded on the dirty -> clean
    # transition, not offered again on the next launch.
    session.undo_stack.undo()
    assert not session.is_dirty
    deadline = time.monotonic() + 2.0
    while store.discover() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert not store.discover()
    controller.close()


def test_autosave_controller_defers_discard_until_a_pending_save_settles(
    qapp: QApplication, tmp_path: Path
) -> None:
    release = threading.Event()
    discarded: list[UUID] = []

    class _BlockingStore:
        def save(
            self,
            snapshot: GraphSnapshot,
            *,
            explicit_path: object = None,
        ) -> Path:
            del explicit_path
            if not release.is_set():
                release.wait(2.0)
            return tmp_path / f"{snapshot.revision}.recovery.synmachine.json"

        def discard(self, document_id: object) -> None:
            discarded.append(cast(UUID, document_id))

    session = DocumentSession(create_utility_registry())
    controller = AutosaveController(cast(AutosaveStore, _BlockingStore()), session=session)
    document_id = session.document.document_id

    session.add_node("synmachine.utility.number", (0.0, 0.0))
    controller.request(session.document.snapshot(), explicit_path=None)

    # The document goes clean while its autosave is still on the worker:
    # the discard must wait for the save, never deleting a file the save
    # is about to write.
    session.undo_stack.undo()
    assert not session.is_dirty
    assert discarded == []
    release.set()

    deadline = time.monotonic() + 2.0
    while not discarded and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert discarded == [document_id]
    controller.close()


def test_autosave_controller_close_completes_a_queued_discard(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    release = threading.Event()
    records: dict[UUID, Path] = {}

    class _BlockingStore:
        def save(
            self,
            snapshot: GraphSnapshot,
            *,
            explicit_path: object = None,
        ) -> Path:
            del explicit_path
            if not release.is_set():
                release.wait(2.0)
            path = tmp_path / f"{snapshot.revision}.recovery.synmachine.json"
            records[snapshot.document_id] = path
            return path

        def discard(self, document_id: UUID) -> None:
            records.pop(document_id, None)

    controller = AutosaveController(cast(AutosaveStore, _BlockingStore()))
    document = GraphDocument()
    document.add_node("test.one")
    document_id = document.document_id

    # The autosave is still on the worker, so the discard for the same
    # document is queued behind it; close() must let it run instead of
    # cancelling it, or the stale record would survive to the next launch.
    controller.request(document.snapshot(), explicit_path=None)
    controller.discard(document_id)
    controller.close()
    assert records == {}
    controller.close()
