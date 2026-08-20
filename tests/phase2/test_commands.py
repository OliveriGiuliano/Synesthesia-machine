"""Exact repeated QUndoStack command behavior for every Phase 2 mutation."""

from uuid import UUID

import pytest
from PySide6.QtGui import QUndoStack

from synesthesia_machine.graph import ConnectionModel, GraphCompiler, GraphDocument, NodeModel
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import copy_fragment, remap_fragment
from synesthesia_machine.ui.commands import (
    PREVIEW_VISIBLE_KEY,
    AddConnectionCommand,
    AddNodeCommand,
    DeleteNodesCommand,
    DuplicateCommand,
    IncompatibleConnectionError,
    MoveNodesCommand,
    PasteCommand,
    RemoveConnectionCommand,
    ReplaceConnectionCommand,
    SetConnectionPreviewCommand,
    SetParameterCommand,
)

NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")
NODE_C = UUID("00000000-0000-0000-0000-00000000000c")
NODE_D = UUID("00000000-0000-0000-0000-00000000000d")
CONNECTION_A = UUID("00000000-0000-0000-0000-00000000001a")
CONNECTION_B = UUID("00000000-0000-0000-0000-00000000001b")


def semantic_state(document: GraphDocument) -> tuple[object, ...]:
    snapshot = document.snapshot()
    return snapshot.document_id, snapshot.nodes, snapshot.connections, snapshot.document_settings


def cycle(
    stack: QUndoStack,
    document: GraphDocument,
    before: tuple[object, ...],
    after: tuple[object, ...],
) -> None:
    for _ in range(3):
        stack.undo()
        assert semantic_state(document) == before
        stack.redo()
        assert semantic_state(document) == after


def test_add_set_and_multi_move_commands_restore_exact_state() -> None:
    document = GraphDocument()
    stack = QUndoStack()
    empty = semantic_state(document)
    first = NodeModel(NODE_A, "synmachine.utility.number", 1, position=(0.0, 0.0))
    stack.push(AddNodeCommand(document, first))
    added = semantic_state(document)
    cycle(stack, document, empty, added)

    second = NodeModel(NODE_B, "synmachine.utility.number", 1, position=(10.0, 10.0))
    stack.push(AddNodeCommand(document, second))
    before_move = semantic_state(document)
    stack.push(
        MoveNodesCommand(
            document,
            {NODE_A: (0.0, 0.0), NODE_B: (10.0, 10.0)},
            {NODE_A: (5.0, 7.0), NODE_B: (15.0, 17.0)},
        )
    )
    assert stack.count() == 3
    moved = semantic_state(document)
    cycle(stack, document, before_move, moved)

    before_parameter = semantic_state(document)
    stack.push(SetParameterCommand(document, NODE_A, "float_value", 8.5))
    changed = semantic_state(document)
    cycle(stack, document, before_parameter, changed)


def test_delete_restores_nodes_and_incident_connections_without_duplicates() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A)
    second = document.add_node("synmachine.utility.math", node_id=NODE_B)
    document.add_connection(first, "value", second, "a", connection_id=CONNECTION_A)
    stack = QUndoStack()
    before = semantic_state(document)

    stack.push(DeleteNodesCommand(document, {first}))
    after = semantic_state(document)
    assert not document.connections
    cycle(stack, document, before, after)
    assert len(document.nodes) == 1
    assert not document.connections


def test_add_remove_and_replace_connections_are_single_exact_commands() -> None:
    registry = create_utility_registry()
    compiler = GraphCompiler(registry)
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A)
    second = document.add_node("synmachine.utility.number", node_id=NODE_B)
    target = document.add_node("synmachine.utility.math", node_id=NODE_C)
    edge_a = ConnectionModel(CONNECTION_A, first, "value", target, "a")
    edge_b = ConnectionModel(CONNECTION_B, second, "value", target, "a")
    stack = QUndoStack()
    before_add = semantic_state(document)

    stack.push(AddConnectionCommand(document, compiler, edge_a))
    after_add = semantic_state(document)
    cycle(stack, document, before_add, after_add)

    before_replace = semantic_state(document)
    stack.push(ReplaceConnectionCommand(document, compiler, edge_b))
    after_replace = semantic_state(document)
    assert stack.count() == 2
    assert document.connections == (edge_b,)
    cycle(stack, document, before_replace, after_replace)

    before_remove = semantic_state(document)
    stack.push(RemoveConnectionCommand(document, CONNECTION_B))
    after_remove = semantic_state(document)
    cycle(stack, document, before_remove, after_remove)


def test_incompatible_connection_is_refused_before_stack_mutation() -> None:
    compiler = GraphCompiler(create_utility_registry())
    document = GraphDocument()
    number = document.add_node("synmachine.utility.number", node_id=NODE_A)
    logic = document.add_node("synmachine.utility.logic", node_id=NODE_B)
    edge = ConnectionModel(CONNECTION_A, number, "value", logic, "a")
    stack = QUndoStack()

    with pytest.raises(IncompatibleConnectionError):
        stack.push(AddConnectionCommand(document, compiler, edge))
    assert stack.count() == 0
    assert not document.connections


@pytest.mark.parametrize("command_type", [PasteCommand, DuplicateCommand])
def test_paste_and_duplicate_restore_remapped_fragment_exactly(
    command_type: type[PasteCommand],
) -> None:
    source = GraphDocument()
    first = source.add_node("synmachine.utility.number", node_id=NODE_A)
    second = source.add_node("synmachine.utility.math", node_id=NODE_B)
    source.add_connection(first, "value", second, "a", connection_id=CONNECTION_A)
    source.set_connection_ui_state(CONNECTION_A, PREVIEW_VISIBLE_KEY, False)
    identifiers = iter((NODE_C, NODE_D, CONNECTION_B))
    fragment = remap_fragment(
        copy_fragment(source.snapshot(), {first, second}), id_factory=lambda: next(identifiers)
    )
    target = GraphDocument()
    stack = QUndoStack()
    before = semantic_state(target)

    stack.push(command_type(target, fragment))
    after = semantic_state(target)
    assert len(target.nodes) == 2
    assert len(target.connections) == 1
    pasted_connection = target.connection(CONNECTION_B)
    assert pasted_connection is not None
    assert pasted_connection.ui_state == {PREVIEW_VISIBLE_KEY: False}
    cycle(stack, target, before, after)


def test_set_connection_preview_visible_is_one_exact_undoable_command() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A)
    second = document.add_node("synmachine.utility.math", node_id=NODE_B)
    document.add_connection(first, "value", second, "a", connection_id=CONNECTION_A)
    stack = QUndoStack()
    before = semantic_state(document)
    assert document.connection(CONNECTION_A).ui_state == {}

    stack.push(SetConnectionPreviewCommand(document, CONNECTION_A, False))
    after = semantic_state(document)
    assert document.connection(CONNECTION_A).ui_state == {PREVIEW_VISIBLE_KEY: False}
    cycle(stack, document, before, after)
    assert document.connection(CONNECTION_A).ui_state == {PREVIEW_VISIBLE_KEY: False}


def test_set_connection_preview_visible_toggles_between_states() -> None:
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number", node_id=NODE_A)
    second = document.add_node("synmachine.utility.math", node_id=NODE_B)
    document.add_connection(first, "value", second, "a", connection_id=CONNECTION_A)
    stack = QUndoStack()

    def visible() -> bool:
        return bool(document.connection(CONNECTION_A).ui_state.get(PREVIEW_VISIBLE_KEY, True))

    assert visible() is True  # absent key is the "visible" default
    stack.push(SetConnectionPreviewCommand(document, CONNECTION_A, False))
    assert visible() is False
    stack.push(SetConnectionPreviewCommand(document, CONNECTION_A, True))
    assert visible() is True
    assert stack.count() == 2
    stack.undo()
    assert visible() is False
    stack.undo()
    assert visible() is True  # undoing back to the original absent state
