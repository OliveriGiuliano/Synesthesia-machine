"""Phase 7 batch 1 canvas groups/comments and exact command semantics."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication

from synesthesia_machine.graph import GraphDocument, GroupKind, GroupModel
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import graph_from_json, graph_to_json
from synesthesia_machine.ui.canvas import GraphScene
from synesthesia_machine.ui.commands import (
    AddGroupCommand,
    DeleteGroupsCommand,
    EditGroupCommand,
    MoveGroupsCommand,
)
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME

GROUP_ID = UUID("70000000-0000-0000-0000-000000000001")
COMMENT_ID = UUID("70000000-0000-0000-0000-000000000002")


def _state(document: GraphDocument) -> tuple[object, ...]:
    snapshot = document.snapshot()
    return snapshot.document_id, snapshot.nodes, snapshot.connections, snapshot.groups


def _cycle(
    stack: QUndoStack,
    document: GraphDocument,
    before: tuple[object, ...],
    after: tuple[object, ...],
) -> None:
    for _ in range(3):
        stack.undo()
        assert _state(document) == before
        stack.redo()
        assert _state(document) == after


def test_group_and_comment_persist_canonically_with_strict_validation() -> None:
    registry = create_utility_registry()
    document = GraphDocument()
    document.add_group(
        GroupKind.GROUP,
        group_id=GROUP_ID,
        title="Analysis",
        position=(10.0, 20.0),
        size=(640.0, 360.0),
        color="#345678",
    )
    document.add_group(
        GroupKind.COMMENT,
        group_id=COMMENT_ID,
        title="Signal note",
        text="Keep this branch in the same source clock.",
        position=(30.0, 40.0),
        size=(320.0, 140.0),
        color="#765432",
    )

    text = graph_to_json(document.snapshot())
    restored = graph_from_json(text, registry)
    assert restored.groups == document.groups
    assert graph_to_json(restored) == text
    assert [item["kind"] for item in json.loads(text)["groups"]] == ["GROUP", "COMMENT"]

    malformed = json.loads(text)
    malformed["groups"][0]["size"] = [0.0, 10.0]
    with pytest.raises(ValueError, match="Group size must be positive"):
        graph_from_json(json.dumps(malformed), registry)


@pytest.mark.parametrize(
    "overrides",
    (
        {"position": (float("nan"), 0.0)},
        {"size": (-1.0, 10.0)},
        {"color": "blue"},
    ),
)
def test_group_domain_rejects_invalid_geometry_and_color(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "id": GROUP_ID,
        "kind": GroupKind.GROUP,
        "title": "Group",
        "text": "",
        "position": (0.0, 0.0),
        "size": (100.0, 100.0),
        "color": "#112233",
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        GroupModel(**values)  # type: ignore[arg-type]


def test_every_group_command_has_exact_repeated_undo_redo() -> None:
    document = GraphDocument()
    stack = QUndoStack()
    group = GroupModel(
        GROUP_ID,
        GroupKind.GROUP,
        "Sources",
        "",
        (0.0, 0.0),
        (400.0, 240.0),
        "#334455",
    )

    empty = _state(document)
    stack.push(AddGroupCommand(document, group))
    added = _state(document)
    _cycle(stack, document, empty, added)

    before_move = _state(document)
    stack.push(MoveGroupsCommand(document, {GROUP_ID: (0.0, 0.0)}, {GROUP_ID: (32.0, 48.0)}))
    moved = _state(document)
    _cycle(stack, document, before_move, moved)

    current = document.group(GROUP_ID)
    assert current is not None
    before_edit = _state(document)
    stack.push(
        EditGroupCommand(
            document,
            replace(current, title="Inputs", text="Camera and deterministic video sources."),
        )
    )
    edited = _state(document)
    _cycle(stack, document, before_edit, edited)

    before_delete = _state(document)
    stack.push(DeleteGroupsCommand(document, {GROUP_ID}))
    deleted = _state(document)
    assert not document.groups
    _cycle(stack, document, before_delete, deleted)


def test_session_scene_projects_selects_moves_and_deletes_groups(qapp: QApplication) -> None:
    del qapp
    session = DocumentSession(create_utility_registry())
    scene = GraphScene(session, DEFAULT_THEME)
    group_id = session.add_group(
        GroupKind.COMMENT,
        (12.0, 24.0),
        title="Remember",
        text="Inspect errors before playback.",
        size=(300.0, 120.0),
    )

    assert group_id in scene.group_items
    scene.select_group_ids({group_id})
    assert scene.selected_group_ids() == {group_id}
    session.move_groups({group_id: (12.0, 24.0)}, {group_id: (44.0, 88.0)})
    assert session.document.group(group_id).position == (44.0, 88.0)  # type: ignore[union-attr]

    scene.select_group_ids({group_id})
    scene.delete_selection()
    assert session.document.group(group_id) is None
    session.undo_stack.undo()
    assert session.document.group(group_id) is not None
