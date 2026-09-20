"""Headless tests for the published graph view-model projection.

The view model is the single surface renderers and the demand policy read:
these tests pin the published fields — the preview-visibility rule, each
node's execution kind and preview dock, the group list, and the derived
slices (demand-root ids, dock routings, pill producers, source facts) that
consumers read instead of re-scanning nodes and connections.
"""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.graph import GraphCompiler, GraphDocument, GroupKind
from synesthesia_machine.nodes import ExecutionKind, PreviewDock
from synesthesia_machine.nodes.composition import create_builtin_registry
from synesthesia_machine.ui.connection_state import PREVIEW_VISIBLE_KEY
from synesthesia_machine.ui.view_models import project_graph

GROUP_ID = UUID("11111111-2222-4333-8444-555555555555")
CONNECTION_KEY = UUID("66666666-7777-4888-8999-000000000000")


def _view_model(document: GraphDocument):
    registry = create_builtin_registry()
    snapshot = document.snapshot()
    compilation = GraphCompiler(registry).compile(snapshot)
    return project_graph(snapshot, registry, compilation.report, compilation=compilation)


def test_preview_visible_defaults_to_true_when_ui_state_is_absent() -> None:
    document = GraphDocument()
    source_id = document.add_node("synmachine.input.load_video")
    display_id = document.add_node("synmachine.visualization.display_image_data")
    document.add_connection(source_id, "image", display_id, "image")

    view = _view_model(document)
    assert [connection.preview_visible for connection in view.connections] == [True]


def test_preview_visible_projects_an_explicit_hide() -> None:
    document = GraphDocument()
    source_id = document.add_node("synmachine.input.load_video")
    display_id = document.add_node("synmachine.visualization.display_image_data")
    connection_id = document.add_connection(source_id, "image", display_id, "image")

    document.set_connection_ui_state(connection_id, PREVIEW_VISIBLE_KEY, False)
    view = _view_model(document)
    assert view.connections[0].preview_visible is False

    document.set_connection_ui_state(connection_id, PREVIEW_VISIBLE_KEY, True)
    view = _view_model(document)
    assert view.connections[0].preview_visible is True


def test_execution_kind_and_is_source_are_published_on_the_node_view() -> None:
    document = GraphDocument()
    source_id = document.add_node("synmachine.input.load_video")
    display_id = document.add_node("synmachine.visualization.display_image_data")

    view = _view_model(document)
    by_id = {node.node_id: node for node in view.nodes}

    assert by_id[source_id].execution_kind is ExecutionKind.SOURCE
    assert by_id[source_id].is_source
    assert by_id[display_id].execution_kind is ExecutionKind.VISUALIZER
    assert not by_id[display_id].is_source


def test_preview_dock_is_published_on_the_node_view() -> None:
    document = GraphDocument()
    image_id = document.add_node("synmachine.visualization.display_image_data")
    note_id = document.add_node("synmachine.visualization.note_visualizer")
    number_id = document.add_node("synmachine.utility.number")

    view = _view_model(document)
    by_id = {node.node_id: node for node in view.nodes}

    assert by_id[image_id].preview_dock is PreviewDock.IMAGE
    assert by_id[note_id].preview_dock is PreviewDock.NOTE
    assert by_id[number_id].preview_dock is None


def test_groups_are_published_on_the_graph_view() -> None:
    document = GraphDocument()
    document.add_group(
        GroupKind.GROUP,
        group_id=GROUP_ID,
        title="Analysis",
        position=(10.0, 20.0),
        size=(300.0, 200.0),
    )

    view = _view_model(document)
    assert [group.id for group in view.groups] == [GROUP_ID]
    assert view.groups[0].title == "Analysis"

    document.remove_group(GROUP_ID)
    view = _view_model(document)
    assert view.groups == ()


def test_derived_slices_publish_the_consumer_facts() -> None:
    document = GraphDocument()
    video_id = document.add_node(
        "synmachine.input.load_video", parameters={"file_path": "unused.mp4"}
    )
    send_id = document.add_node("synmachine.output.send_midi")
    image_id = document.add_node("synmachine.visualization.display_image_data")
    note_id = document.add_node("synmachine.visualization.note_visualizer")
    connection_id = document.add_connection(video_id, "image", image_id, "image")

    view = _view_model(document)
    derived = view.derived

    assert derived.source_node_ids == (video_id,)
    assert derived.sink_node_ids == (send_id,)
    assert derived.image_visualizer_ids == (image_id,)
    assert derived.note_visualizer_ids == (note_id,)
    assert derived.image_dock_source_keys == frozenset({(video_id, "image")})
    assert derived.pill_producer_ids == (video_id,)
    # Absent means visible: the rule is published, not re-interpreted.
    assert derived.preview_visibilities == {connection_id: True}
    # File-bearing sources publish the deterministic (type_id, file_path)
    # facts the export-eligibility cache keys on.
    assert derived.source_facts == (("synmachine.input.load_video", "unused.mp4"),)
