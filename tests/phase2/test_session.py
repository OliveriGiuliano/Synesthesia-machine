"""DocumentSession command grouping, persistence, and recovery semantics."""

from pathlib import Path

from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.ui.session import DocumentSession


def semantic_state(session: DocumentSession) -> tuple[object, ...]:
    snapshot = session.document.snapshot()
    return snapshot.document_id, snapshot.nodes, snapshot.connections, snapshot.document_settings


def test_delete_mixed_selection_is_one_exact_undo_macro() -> None:
    session = DocumentSession(create_utility_registry())
    first = session.add_node("synmachine.utility.number", (0.0, 0.0))
    second = session.add_node("synmachine.utility.number", (0.0, 100.0))
    target = session.add_node("synmachine.utility.math", (250.0, 0.0))
    incident = session.add_connection(first, "value", target, "a")
    independent = session.add_connection(second, "value", target, "b")
    before = semantic_state(session)
    count_before = session.undo_stack.count()

    session.delete_selection({first}, {incident, independent})
    after = semantic_state(session)

    assert session.undo_stack.count() == count_before + 1
    assert session.document.node(first) is None
    assert not session.document.connections
    session.undo_stack.undo()
    assert semantic_state(session) == before
    session.undo_stack.redo()
    assert semantic_state(session) == after


def test_insert_and_connect_is_one_exact_undo_macro() -> None:
    registry = create_utility_registry()
    session = DocumentSession(registry)
    source = session.add_node("synmachine.utility.number", (0.0, 0.0))
    before = semantic_state(session)
    count_before = session.undo_stack.count()

    inserted = session.insert_and_connect(
        registry.require("synmachine.utility.math"),
        "a",
        source,
        "value",
        True,
        (260.0, 40.0),
    )
    after = semantic_state(session)

    assert session.undo_stack.count() == count_before + 1
    assert session.document.node(inserted) is not None
    assert len(session.document.connections) == 1
    session.undo_stack.undo()
    assert semantic_state(session) == before
    session.undo_stack.redo()
    assert semantic_state(session) == after


def test_save_open_and_recovery_preserve_values_and_clean_state(tmp_path: Path) -> None:
    registry = create_utility_registry()
    session = DocumentSession(registry)
    node_id = session.add_node("synmachine.utility.number", (123.5, -44.0))
    session.set_parameter(node_id, "number_type", "FLOAT")
    session.set_parameter(node_id, "float_value", 7.25)
    path = tmp_path / "roundtrip.synmachine.json"

    session.save(path)
    assert not session.is_dirty

    reopened = DocumentSession(registry)
    reopened.open_document(path)
    node = reopened.document.node(node_id)
    assert node is not None
    assert node.position == (123.5, -44.0)
    assert node.parameters["float_value"] == 7.25
    assert not reopened.is_dirty
    assert reopened.undo_stack.count() == 0

    recovered = DocumentSession(registry)
    recovered.recover_document(path)
    assert recovered.document.snapshot().document_id == session.document.document_id
    assert recovered.is_dirty
    assert recovered.current_path is None
