"""Current catalogue, example-graph, and synthesis benchmark acceptance tests."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID

import pytest
from tools.synesthesia_benchmarks import run_benchmarks

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphCompiler, GraphSnapshot
from synesthesia_machine.persistence import graph_from_json, graph_to_json, load_graph

EXAMPLE_ROOT = Path("examples/library")
CATALOGUE_PATH = Path("examples/catalogue/current.synmachine.json")
COMPATIBILITY_CATALOGUE_PATH = Path(
    "tests/fixtures/compatibility/catalogues/catalogue-50-definitions.synmachine.json"
)
GRAPH_PATHS = (
    EXAMPLE_ROOT / "motion-grid.synmachine.json",
    EXAMPLE_ROOT / "edge-ensemble.synmachine.json",
    EXAMPLE_ROOT / "scanning-score.synmachine.json",
    EXAMPLE_ROOT / "spatial-spectrum.synmachine.json",
)

MOTION_SOURCE_ID = UUID("65100000-0000-0000-0000-000000000001")
MOTION_RESIZE_ID = UUID("65100000-0000-0000-0000-000000000002")
MOTION_HOLD_ID = UUID("65100000-0000-0000-0000-000000000003")
MOTION_FLOW_ID = UUID("65100000-0000-0000-0000-000000000004")
MOTION_NOTES_ID = UUID("65100000-0000-0000-0000-000000000005")

EXPECTED_NODE_IDS = {
    "motion-grid.synmachine.json": tuple(
        UUID(f"65100000-0000-0000-0000-{index:012d}") for index in range(1, 6)
    ),
    "edge-ensemble.synmachine.json": tuple(
        UUID(f"65200000-0000-0000-0000-{index:012d}") for index in range(1, 6)
    ),
    "scanning-score.synmachine.json": tuple(
        UUID(f"65300000-0000-0000-0000-{index:012d}") for index in range(1, 6)
    ),
    "spatial-spectrum.synmachine.json": tuple(
        UUID(f"65400000-0000-0000-0000-{index:012d}") for index in range(1, 6)
    ),
}

EXPECTED_TYPE_IDS = {
    "motion-grid.synmachine.json": (
        "synmachine.input.load_video",
        "synmachine.image.resize",
        "synmachine.image.hold_image",
        "synmachine.synesthesia.optical_flow",
        "synmachine.visualization.note_visualizer",
    ),
    "edge-ensemble.synmachine.json": (
        "synmachine.input.load_video",
        "synmachine.image.canny",
        "synmachine.synesthesia.edges_to_pitch",
        "synmachine.utility.multiply_velocity",
        "synmachine.visualization.note_visualizer",
    ),
    "scanning-score.synmachine.json": (
        "synmachine.input.load_video",
        "synmachine.image.change_colour_space",
        "synmachine.image.separate_channels",
        "synmachine.synesthesia.scanline",
        "synmachine.visualization.note_visualizer",
    ),
    "spatial-spectrum.synmachine.json": (
        "synmachine.input.load_video",
        "synmachine.image.to_luminance",
        "synmachine.synesthesia.fourier",
        "synmachine.utility.transpose",
        "synmachine.output.generate_audio",
    ),
}

SYNESTHESIA_DESCRIPTION_DETAILS = {
    "synmachine.synesthesia.channel_to_pitch": (
        "histogram",
        "parameter a/b",
        "occupancy controls velocity",
        "polyphony limit",
    ),
    "synmachine.synesthesia.scanline": (
        "processed frame",
        "left-to-right",
        "activation threshold",
        "resets restart",
    ),
    "synmachine.synesthesia.edges_to_pitch": (
        "contours",
        "nested shapes",
        "orientation",
        "explicit ranges",
    ),
    "synmachine.synesthesia.fourier": (
        "fft",
        "spatial frequencies",
        "one band per allowed note",
        "cache affects performance only",
    ),
    "synmachine.synesthesia.optical_flow": (
        "farnebäck",
        "reference image",
        "grid cells",
        "previous or held frame",
    ),
    "synmachine.synesthesia.region_grid": (
        "row-major order",
        "rms contrast",
        "activation threshold",
        "controls velocity",
    ),
}


def test_catalogue_is_the_exact_current_65_definition_registry() -> None:
    registry = create_application_registry()
    snapshot = load_graph(CATALOGUE_PATH, registry)
    expected = tuple(definition.type_id for definition in registry.definitions())
    actual = tuple(node.type_id for node in snapshot.nodes)

    assert len(expected) == len(actual) == len(set(actual)) == 65
    assert actual == expected
    assert not snapshot.connections

    compilation = GraphCompiler(registry).compile(snapshot)
    assert compilation.plan is None
    assert {issue.code for issue in compilation.report.errors} <= {
        "required_input_missing",
        "unresolved_generic_type",
    }


def test_complex_synesthesia_nodes_have_explanatory_hover_descriptions() -> None:
    registry = create_application_registry()

    for type_id, expected_details in SYNESTHESIA_DESCRIPTION_DETAILS.items():
        definition = registry.require(type_id)
        description = definition.description.casefold()

        assert definition.category == "Synesthesia"
        assert len(definition.description) >= 250
        assert all(detail in description for detail in expected_details)


def test_compatibility_catalogue_remains_an_exact_50_node_historical_subset() -> None:
    registry = create_application_registry()
    compatibility = load_graph(COMPATIBILITY_CATALOGUE_PATH, registry)
    current = load_graph(CATALOGUE_PATH, registry)
    compatibility_ids = tuple(node.type_id for node in compatibility.nodes)
    current_ids = tuple(node.type_id for node in current.nodes)

    assert len(compatibility_ids) == len(set(compatibility_ids)) == 50
    assert set(compatibility_ids) < set(current_ids)


def test_current_persisted_artifacts_have_canonical_schema_round_trips() -> None:
    registry = create_application_registry()
    paths = (CATALOGUE_PATH, *sorted(EXAMPLE_ROOT.glob("*.synmachine.json")))
    assert tuple(CATALOGUE_PATH.parent.glob("*.synmachine.json")) == (CATALOGUE_PATH,)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert graph_to_json(graph_from_json(text, registry)) == text


@pytest.mark.parametrize("path", GRAPH_PATHS, ids=lambda path: path.stem)
def test_reference_graph_production_loads_and_compiles_without_midi_hardware(path: Path) -> None:
    registry = create_application_registry()
    snapshot = load_graph(path, registry)
    result = GraphCompiler(registry).compile(snapshot)

    assert result.report.is_valid
    assert result.plan is not None
    assert tuple(node.node_id for node in result.plan.nodes) == EXPECTED_NODE_IDS[path.name]
    assert tuple(node.type_id for node in snapshot.nodes) == EXPECTED_TYPE_IDS[path.name]
    assert all(node.type_id != "synmachine.output.send_midi" for node in snapshot.nodes)
    _assert_portable_source(snapshot)

    audio_nodes = [
        node for node in snapshot.nodes if node.type_id == "synmachine.output.generate_audio"
    ]
    assert all(node.parameters["enabled"] is False for node in audio_nodes)


def test_motion_grid_uses_current_and_one_frame_held_reference_from_one_source() -> None:
    registry = create_application_registry()
    snapshot = load_graph(GRAPH_PATHS[0], registry)
    hold = snapshot.node(MOTION_HOLD_ID)
    assert hold is not None and hold.parameters["delay_frames"] == 1

    assert _edges(snapshot) == {
        (MOTION_SOURCE_ID, "image", MOTION_RESIZE_ID, "image"),
        (MOTION_RESIZE_ID, "image", MOTION_HOLD_ID, "image"),
        (MOTION_RESIZE_ID, "image", MOTION_FLOW_ID, "current"),
        (MOTION_HOLD_ID, "image", MOTION_FLOW_ID, "reference"),
        (MOTION_FLOW_ID, "midi", MOTION_NOTES_ID, "midi"),
    }
    assert sum(node.type_id == "synmachine.input.load_video" for node in snapshot.nodes) == 1


def test_reference_graphs_have_the_recommended_synesthesia_topologies() -> None:
    registry = create_application_registry()
    expected_edges = {
        "edge-ensemble.synmachine.json": (
            ("synmachine.input.load_video", "image", "synmachine.image.canny", "image"),
            (
                "synmachine.image.canny",
                "channel",
                "synmachine.synesthesia.edges_to_pitch",
                "edges",
            ),
            (
                "synmachine.synesthesia.edges_to_pitch",
                "midi",
                "synmachine.utility.multiply_velocity",
                "midi",
            ),
            (
                "synmachine.utility.multiply_velocity",
                "midi",
                "synmachine.visualization.note_visualizer",
                "midi",
            ),
        ),
        "scanning-score.synmachine.json": (
            (
                "synmachine.input.load_video",
                "image",
                "synmachine.image.change_colour_space",
                "image",
            ),
            (
                "synmachine.image.change_colour_space",
                "image",
                "synmachine.image.separate_channels",
                "image",
            ),
            (
                "synmachine.image.separate_channels",
                "channel_2",
                "synmachine.synesthesia.scanline",
                "value",
            ),
            (
                "synmachine.synesthesia.scanline",
                "midi",
                "synmachine.visualization.note_visualizer",
                "midi",
            ),
        ),
        "spatial-spectrum.synmachine.json": (
            (
                "synmachine.input.load_video",
                "image",
                "synmachine.image.to_luminance",
                "image",
            ),
            (
                "synmachine.image.to_luminance",
                "channel",
                "synmachine.synesthesia.fourier",
                "value",
            ),
            (
                "synmachine.synesthesia.fourier",
                "midi",
                "synmachine.utility.transpose",
                "midi",
            ),
            (
                "synmachine.utility.transpose",
                "midi",
                "synmachine.output.generate_audio",
                "midi",
            ),
        ),
    }
    for path in GRAPH_PATHS[1:]:
        snapshot = load_graph(path, registry)
        assert _typed_edges(snapshot) == set(expected_edges[path.name])


def test_fast_benchmark_writes_stable_500x500_states_and_valid_timings(tmp_path: Path) -> None:
    first = run_benchmarks(
        output_path=tmp_path / "first.json",
        warmup_runs=1,
        measured_runs=2,
    )
    second = run_benchmarks(
        output_path=tmp_path / "second.json",
        warmup_runs=1,
        measured_runs=2,
    )

    assert first.passed and second.passed
    assert tuple(item.algorithm for item in first.benchmarks) == (
        "Scanline",
        "Edges to Pitch",
        "Fourier",
        "Optical Flow",
    )
    assert tuple(item.fixture_sha256 for item in first.benchmarks) == tuple(
        item.fixture_sha256 for item in second.benchmarks
    )
    assert tuple(item.output_notes for item in first.benchmarks) == tuple(
        item.output_notes for item in second.benchmarks
    )
    for item in first.benchmarks:
        assert item.input_shape == (500, 500)
        assert item.warmup_runs == 1 and item.measured_runs == 2
        assert item.output_state_stable and item.output_notes
        assert len(item.timing.samples_ms) == 2
        assert all(math.isfinite(sample) and sample >= 0.0 for sample in item.timing.samples_ms)
        assert item.timing.minimum_ms <= item.timing.median_ms <= item.timing.maximum_ms
        assert item.timing.minimum_ms <= item.timing.p95_ms <= item.timing.maximum_ms

    persisted = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 1
    assert persisted["methodology"]["input_resolution"] == [500, 500]
    assert persisted["passed"] is True
    assert (tmp_path / "first.json").read_bytes().endswith(b"\n")


@pytest.mark.parametrize(("warmup_runs", "measured_runs"), ((0, 1), (1, 0), (-1, 2)))
def test_benchmark_rejects_invalid_run_counts(
    tmp_path: Path,
    warmup_runs: int,
    measured_runs: int,
) -> None:
    with pytest.raises(ValueError, match="must be at least 1"):
        run_benchmarks(
            output_path=tmp_path / "invalid.json",
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
        )


def _assert_portable_source(snapshot: GraphSnapshot) -> None:
    sources = [node for node in snapshot.nodes if node.type_id == "synmachine.input.load_video"]
    assert len(sources) == 1
    source_path = Path(str(sources[0].parameters["file_path"]))
    assert source_path == Path("examples/library/media/reference.mp4").resolve()
    assert source_path.is_file()
    assert sources[0].parameters["loop"] is True


def _edges(snapshot: GraphSnapshot) -> set[tuple[UUID, str, UUID, str]]:
    return {
        (
            connection.source_node_id,
            connection.source_port_id,
            connection.destination_node_id,
            connection.destination_port_id,
        )
        for connection in snapshot.connections
    }


def _typed_edges(snapshot: GraphSnapshot) -> set[tuple[str, str, str, str]]:
    node_types = {node.id: node.type_id for node in snapshot.nodes}
    return {
        (
            node_types[connection.source_node_id],
            connection.source_port_id,
            node_types[connection.destination_node_id],
            connection.destination_port_id,
        )
        for connection in snapshot.connections
    }
