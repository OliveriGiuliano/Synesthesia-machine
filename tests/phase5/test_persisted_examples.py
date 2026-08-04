"""Persisted Phase 5 reference and full-catalogue acceptance tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphCompiler
from synesthesia_machine.persistence import graph_from_json, graph_to_json, load_graph

EXAMPLE_ROOT = Path("examples/phase5")
CATALOGUE_PATH = EXAMPLE_ROOT / "catalogue.synmachine.json"
REFERENCE_PATH = EXAMPLE_ROOT / "reference_graph.synmachine.json"
REFERENCE_MEDIA_PATH = EXAMPLE_ROOT / "media/phase5_reference.mp4"

SOURCE_ID = UUID("52000000-0000-0000-0000-000000000001")
RESIZE_ID = UUID("52000000-0000-0000-0000-000000000002")
BLUR_ID = UUID("52000000-0000-0000-0000-000000000003")
COLOUR_ID = UUID("52000000-0000-0000-0000-000000000004")
SEPARATE_ID = UUID("52000000-0000-0000-0000-000000000005")
PITCH_ID = UUID("52000000-0000-0000-0000-000000000006")
NOTE_DISPLAY_ID = UUID("52000000-0000-0000-0000-000000000007")
CANNY_ID = UUID("52000000-0000-0000-0000-000000000008")
CHANNEL_DISPLAY_ID = UUID("52000000-0000-0000-0000-000000000009")


def test_catalogue_loads_every_registry_definition_exactly_once() -> None:
    registry = create_application_registry()
    snapshot = load_graph(CATALOGUE_PATH, registry)
    expected = tuple(definition.type_id for definition in registry.definitions())
    actual = tuple(node.type_id for node in snapshot.nodes)

    assert actual == expected
    assert len(actual) == len(set(actual)) == 51
    assert not snapshot.connections

    compilation = GraphCompiler(registry).compile(snapshot)
    assert compilation.plan is None
    assert {issue.code for issue in compilation.report.errors} <= {
        "required_input_missing",
        "unresolved_generic_type",
    }


def test_reference_graph_loads_and_compiles_with_exact_phase5_branches() -> None:
    registry = create_application_registry()
    snapshot = load_graph(REFERENCE_PATH, registry)
    result = GraphCompiler(registry).compile(snapshot)

    assert result.report.is_valid
    assert result.plan is not None
    assert tuple(node.node_id for node in result.plan.nodes) == (
        SOURCE_ID,
        RESIZE_ID,
        BLUR_ID,
        COLOUR_ID,
        SEPARATE_ID,
        PITCH_ID,
        NOTE_DISPLAY_ID,
        CANNY_ID,
        CHANNEL_DISPLAY_ID,
    )

    source = snapshot.node(SOURCE_ID)
    resize = snapshot.node(RESIZE_ID)
    blur = snapshot.node(BLUR_ID)
    colour = snapshot.node(COLOUR_ID)
    canny = snapshot.node(CANNY_ID)
    assert source is not None
    assert source.parameters["loop"] is True
    assert Path(str(source.parameters["file_path"])) == REFERENCE_MEDIA_PATH.resolve()
    assert resize is not None
    assert (resize.parameters["width"], resize.parameters["height"]) == (500, 500)
    assert resize.parameters["preserve_aspect"] is False
    assert blur is not None
    assert (blur.parameters["kernel_width"], blur.parameters["kernel_height"]) == (5, 5)
    assert colour is not None and colour.parameters["target_colour_space"] == "HSV"
    assert canny is not None
    assert (canny.parameters["low_threshold"], canny.parameters["high_threshold"]) == (0.1, 0.3)

    edges = {
        (
            connection.source_node_id,
            connection.source_port_id,
            connection.destination_node_id,
            connection.destination_port_id,
        )
        for connection in snapshot.connections
    }
    assert (COLOUR_ID, "image", CANNY_ID, "image") in edges
    assert (CANNY_ID, "channel", CHANNEL_DISPLAY_ID, "channel") in edges
    assert (SEPARATE_ID, "channel_1", PITCH_ID, "value") in edges
    assert (PITCH_ID, "midi", NOTE_DISPLAY_ID, "midi") in edges


def test_phase5_examples_have_canonical_schema_round_trips() -> None:
    registry = create_application_registry()
    for path in (CATALOGUE_PATH, REFERENCE_PATH):
        text = path.read_text(encoding="utf-8")
        assert graph_to_json(graph_from_json(text, registry)) == text
