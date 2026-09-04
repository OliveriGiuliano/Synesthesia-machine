"""Sequential migrations, compatibility fixtures, and validator diagnostics."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from tools.validate_graphs import validate_paths

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphCompiler
from synesthesia_machine.persistence import (
    GraphPersistenceError,
    NodeMigrationRegistry,
    graph_from_json,
    migrate_adjustment_channel_selection_v1_to_v2,
    migrate_change_colour_space_v1_to_v2,
    migrate_channel_display_v1_to_v2,
    migrate_clamp_v1_to_v2,
    migrate_combine_channels_v1_to_v2,
    migrate_display_image_data_v1_to_v2,
    migrate_hue_v1_to_v2,
    migrate_invert_colour_v1_to_v2,
    migrate_separate_channels_v1_to_v2,
    migrate_statistics_v1_to_v2,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
)
from synesthesia_machine.persistence.schemas import JsonObject, JsonValue

FIXTURES = Path(__file__).parents[1] / "fixtures" / "compatibility" / "migrations"


def test_legacy_graph_and_number_node_fixture_migrate_without_mutation() -> None:
    text = (FIXTURES / "legacy_number_v0.synmachine.json").read_text(encoding="utf-8")
    original = json.loads(text)
    snapshot = graph_from_json(text, create_application_registry())

    assert json.loads(text) == original
    assert snapshot.nodes[0].implementation_version == 1
    assert snapshot.nodes[0].parameters["number_type"] == "FLOAT"
    assert snapshot.nodes[0].parameters["float_value"] == 7.5


def test_legacy_load_video_parameter_is_migrated() -> None:
    text = (FIXTURES / "legacy_video_node_v0.synmachine.json").read_text(encoding="utf-8")
    snapshot = graph_from_json(text, create_application_registry())

    assert snapshot.nodes[0].implementation_version == 1
    assert snapshot.nodes[0].parameters["file_path"] == "media/legacy.mp4"
    assert "path" not in snapshot.nodes[0].parameters


def test_legacy_hue_turns_are_normalized_purely_into_one_turn() -> None:
    source = {
        "id": "70000000-0000-0000-0000-000000000018",
        "type_id": "synmachine.image.hue",
        "implementation_version": 1,
        "parameters": {"turns": -1.25},
    }
    original = deepcopy(source)

    migrated = migrate_hue_v1_to_v2(source)  # type: ignore[arg-type]

    assert source == original
    assert migrated["implementation_version"] == 2
    assert migrated["parameters"] == {"turns": 0.75}


@pytest.mark.parametrize(
    ("type_id", "migrate"),
    [
        ("synmachine.visualization.display_image_data", migrate_display_image_data_v1_to_v2),
        ("synmachine.visualization.channel_display", migrate_channel_display_v1_to_v2),
    ],
)
def test_legacy_display_preview_params_are_dropped_purely(
    type_id: str,
    migrate: Callable[[JsonObject], JsonObject],
) -> None:
    source = {
        "id": "70000000-0000-0000-0000-000000000040",
        "type_id": type_id,
        "implementation_version": 1,
        "parameters": {"preview_fps": 15, "max_dimension": 512, "fit_mode": "FILL"},
    }
    original = deepcopy(source)

    migrated = migrate(source)

    assert source == original
    assert migrated["implementation_version"] == 2
    assert migrated["parameters"] == {"fit_mode": "FILL"}


def test_registry_applies_multiple_steps_sequentially_and_purely() -> None:
    def step(target_version: int):  # type: ignore[no-untyped-def]
        def migrate(data):  # type: ignore[no-untyped-def]
            migrated = deepcopy(data)
            migrated["implementation_version"] = target_version
            migrated["parameters"][f"step_{target_version}"] = True
            return migrated

        return migrate

    registry = NodeMigrationRegistry({("test.node", 0): step(1), ("test.node", 1): step(2)})
    source = {
        "id": "70000000-0000-0000-0000-000000000020",
        "type_id": "test.node",
        "implementation_version": 0,
        "parameters": {},
    }
    original = deepcopy(source)

    result = registry.migrate(source, type_id="test.node", target_version=2)  # type: ignore[arg-type]

    assert source == original
    assert tuple((step.from_version, step.to_version) for step in result.steps) == ((0, 1), (1, 2))
    assert result.data["parameters"] == {"step_1": True, "step_2": True}


@pytest.mark.parametrize(
    ("payload", "code", "detail"),
    [
        ("{broken", "invalid_json", "line 1"),
        (
            json.dumps(
                {
                    "connections": [],
                    "document_id": "70000000-0000-0000-0000-000000000030",
                    "nodes": [
                        {
                            "id": "70000000-0000-0000-0000-000000000031",
                            "type_id": "missing.node",
                            "version": 1,
                        }
                    ],
                }
            ),
            "unknown_node_type",
            "missing.node",
        ),
    ],
)
def test_corrupt_and_unknown_graph_errors_are_actionable(
    payload: str,
    code: str,
    detail: str,
) -> None:
    with pytest.raises(GraphPersistenceError) as captured:
        graph_from_json(payload, create_application_registry())

    assert captured.value.code == code
    assert detail in str(captured.value)


def test_validator_reports_fixture_migrations_and_invalid_file(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.synmachine.json"
    corrupt.write_text("[]", encoding="utf-8")

    report = validate_paths((FIXTURES, corrupt))

    assert report.checked_files == 3
    assert report.valid_files == 2
    assert report.invalid_files == 1
    assert report.migrated_files == 2
    legacy = next(result for result in report.results if "legacy_number" in result.path)
    assert legacy.graph_migration_steps == 4
    assert legacy.node_migration_steps == 1


STAT_SOURCE_ID = "70000000-0000-0000-0000-000000000301"
STAT_BUFFER_ID = "70000000-0000-0000-0000-000000000302"
STAT_STATS_ID = "70000000-0000-0000-0000-000000000303"
STAT_DOC_ID = "70000000-0000-0000-0000-000000000300"


def test_statistics_v1_node_bumps_to_v2_purely() -> None:
    source = {
        "id": STAT_STATS_ID,
        "type_id": "synmachine.utility.statistics",
        "implementation_version": 1,
        "parameters": {"statistic": "MEAN", "percentile": 50.0},
    }
    original = deepcopy(source)

    migrated = migrate_statistics_v1_to_v2(source)  # type: ignore[arg-type]

    assert source == original
    assert migrated["implementation_version"] == 2
    assert migrated["parameters"] == {"statistic": "MEAN", "percentile": 50.0}


def test_v2_statistics_input_moves_to_first_variadic_socket_purely() -> None:
    source = {
        "schema_version": 2,
        "application_version": "0.1.0",
        "document_id": STAT_DOC_ID,
        "nodes": [
            {
                "id": STAT_STATS_ID,
                "type_id": "synmachine.utility.statistics",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"statistic": "MEAN", "percentile": 50.0},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
            {
                "id": STAT_BUFFER_ID,
                "type_id": "synmachine.utility.buffer",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"capacity": 4},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
        ],
        "connections": [
            {
                "id": "70000000-0000-0000-0000-000000000311",
                "source_node_id": STAT_BUFFER_ID,
                "source_port_id": "values",
                "destination_node_id": STAT_STATS_ID,
                "destination_port_id": "values",
                "ui_state": {},
            }
        ],
        "groups": [],
        "document_settings": {},
        "ui_state": {},
    }
    original = deepcopy(source)

    migrated = migrate_v2_to_v3(source)  # type: ignore[arg-type]

    assert source == original
    assert migrated["schema_version"] == 3
    connections = cast("list[JsonValue]", migrated["connections"])
    connection = cast("dict[str, JsonValue]", connections[0])
    assert connection["destination_port_id"] == "values_1"
    assert connection["source_port_id"] == "values"


def test_saved_buffer_statistics_graph_migrates_and_compiles() -> None:
    payload = {
        "schema_version": 2,
        "application_version": "0.1.0",
        "document_id": STAT_DOC_ID,
        "nodes": [
            {
                "id": STAT_SOURCE_ID,
                "type_id": "synmachine.utility.number",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"number_type": "FLOAT", "float_value": 2.0},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
            {
                "id": STAT_BUFFER_ID,
                "type_id": "synmachine.utility.buffer",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"capacity": 4},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
            {
                "id": STAT_STATS_ID,
                "type_id": "synmachine.utility.statistics",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"statistic": "MEAN", "percentile": 50.0},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
        ],
        "connections": [
            {
                "id": "70000000-0000-0000-0000-000000000321",
                "source_node_id": STAT_SOURCE_ID,
                "source_port_id": "value",
                "destination_node_id": STAT_BUFFER_ID,
                "destination_port_id": "value",
                "ui_state": {},
            },
            {
                "id": "70000000-0000-0000-0000-000000000322",
                "source_node_id": STAT_BUFFER_ID,
                "source_port_id": "values",
                "destination_node_id": STAT_STATS_ID,
                "destination_port_id": "values",
                "ui_state": {},
            },
        ],
        "groups": [],
        "document_settings": {},
        "ui_state": {},
    }
    import json as _json

    snapshot = graph_from_json(_json.dumps(payload), create_application_registry())

    statistics = next(
        node
        for node in snapshot.nodes
        if node.id.hex.startswith("70000000") and node.type_id == "synmachine.utility.statistics"
    )
    assert statistics.implementation_version == 2
    migrated_port = next(
        connection
        for connection in snapshot.connections
        if connection.destination_node_id == statistics.id
    )
    assert migrated_port.destination_port_id == "values_1"

    result = GraphCompiler(create_application_registry()).compile(snapshot)
    assert result.report.is_valid, [issue.message for issue in result.report.issues]


ALPHA_DOC_ID = "70000000-0000-0000-0000-000000000500"
ALPHA_VIDEO_ID = "70000000-0000-0000-0000-000000000501"
ALPHA_SEPARATE_ID = "70000000-0000-0000-0000-000000000502"
ALPHA_COMBINE_ID = "70000000-0000-0000-0000-000000000503"
ALPHA_CCS_ID = "70000000-0000-0000-0000-000000000504"
ALPHA_CLAMP_ID = "70000000-0000-0000-0000-000000000505"
ALPHA_INVERT_ID = "70000000-0000-0000-0000-000000000506"
ALPHA_BRIGHT_ID = "70000000-0000-0000-0000-000000000507"
ALPHA_OPACITY_ID = "70000000-0000-0000-0000-000000000508"


def _alpha_v3_payload() -> dict[str, object]:
    def node(node_id: str, type_id: str, version: int, **parameters: object) -> dict[str, object]:
        return {
            "id": node_id,
            "type_id": type_id,
            "implementation_version": version,
            "position": [0.0, 0.0],
            "size": None,
            "parameters": parameters,
            "ui_state": {},
            "user_label": None,
            "collapsed": False,
        }

    def connection(
        connection_id: str,
        source_id: str,
        source_port: str,
        destination_id: str,
        destination_port: str,
    ) -> dict[str, object]:
        return {
            "id": connection_id,
            "source_node_id": source_id,
            "source_port_id": source_port,
            "destination_node_id": destination_id,
            "destination_port_id": destination_port,
            "ui_state": {},
        }

    return {
        "schema_version": 3,
        "application_version": "0.1.0",
        "document_id": ALPHA_DOC_ID,
        "nodes": [
            node(ALPHA_VIDEO_ID, "synmachine.input.load_video", 1, file_path="media/reference.mp4"),
            node(ALPHA_SEPARATE_ID, "synmachine.image.separate_channels", 1),
            node(
                ALPHA_COMBINE_ID, "synmachine.image.combine_channels", 1, target_colour_space="RGBA"
            ),
            node(
                ALPHA_CCS_ID, "synmachine.image.change_colour_space", 1, target_colour_space="RGBA"
            ),
            node(
                ALPHA_CLAMP_ID,
                "synmachine.image.clamp",
                1,
                minimum=0.0,
                maximum=1.0,
                channels="CHANNEL_4",
                include_alpha=True,
            ),
            node(ALPHA_INVERT_ID, "synmachine.image.invert_colour", 1, invert_alpha=True),
            node(
                ALPHA_BRIGHT_ID, "synmachine.image.brightness", 1, offset=0.0, channels="CHANNEL_4"
            ),
            node(ALPHA_OPACITY_ID, "synmachine.image.opacity", 1, factor=0.5),
        ],
        "connections": [
            connection(
                "70000000-0000-0000-0000-000000000510",
                ALPHA_VIDEO_ID,
                "image",
                ALPHA_SEPARATE_ID,
                "image",
            ),
            connection(
                "70000000-0000-0000-0000-000000000511",
                ALPHA_SEPARATE_ID,
                "channel_1",
                ALPHA_COMBINE_ID,
                "channel_1",
            ),
            connection(
                "70000000-0000-0000-0000-000000000512",
                ALPHA_SEPARATE_ID,
                "channel_2",
                ALPHA_COMBINE_ID,
                "channel_2",
            ),
            connection(
                "70000000-0000-0000-0000-000000000513",
                ALPHA_SEPARATE_ID,
                "channel_3",
                ALPHA_COMBINE_ID,
                "channel_3",
            ),
            connection(
                "70000000-0000-0000-0000-000000000514",
                ALPHA_SEPARATE_ID,
                "channel_4",
                ALPHA_COMBINE_ID,
                "channel_4",
            ),
            connection(
                "70000000-0000-0000-0000-000000000515",
                ALPHA_COMBINE_ID,
                "image",
                ALPHA_CCS_ID,
                "image",
            ),
            connection(
                "70000000-0000-0000-0000-000000000516",
                ALPHA_CCS_ID,
                "image",
                ALPHA_CLAMP_ID,
                "image",
            ),
            connection(
                "70000000-0000-0000-0000-000000000517",
                ALPHA_CLAMP_ID,
                "image",
                ALPHA_INVERT_ID,
                "image",
            ),
            connection(
                "70000000-0000-0000-0000-000000000518",
                ALPHA_INVERT_ID,
                "image",
                ALPHA_BRIGHT_ID,
                "image",
            ),
            connection(
                "70000000-0000-0000-0000-000000000519",
                ALPHA_BRIGHT_ID,
                "image",
                ALPHA_OPACITY_ID,
                "image",
            ),
        ],
        "groups": [],
        "document_settings": {},
        "ui_state": {},
    }


def test_v3_to_v4_drops_opacity_and_alpha_surfaces_purely() -> None:
    source = _alpha_v3_payload()
    original = deepcopy(source)

    migrated = migrate_v3_to_v4(source)  # type: ignore[arg-type]

    assert source == original
    assert migrated["schema_version"] == 4
    nodes = cast("list[JsonObject]", migrated["nodes"])
    type_ids = [str(item["type_id"]) for item in nodes]
    assert "synmachine.image.opacity" not in type_ids
    assert len(type_ids) == 7

    connections = cast("list[JsonObject]", migrated["connections"])
    assert len(connections) == 8  # channel_4 bridge and opacity input are dropped
    touched = {str(item["source_node_id"]) for item in connections} | {
        str(item["destination_node_id"]) for item in connections
    }
    assert ALPHA_OPACITY_ID not in touched

    # Parameter rewrites (RGBA target, CHANNEL_4 selection) are applied by the
    # node migrations; the graph migration is structural only.
    type_set = set(type_ids)
    assert type_set == {
        "synmachine.input.load_video",
        "synmachine.image.separate_channels",
        "synmachine.image.combine_channels",
        "synmachine.image.change_colour_space",
        "synmachine.image.clamp",
        "synmachine.image.invert_colour",
        "synmachine.image.brightness",
    }


@pytest.mark.parametrize(
    ("migrate", "parameters"),
    [
        (migrate_separate_channels_v1_to_v2, {}),
        (migrate_combine_channels_v1_to_v2, {"target_colour_space": "RGBA"}),
        (migrate_change_colour_space_v1_to_v2, {"target_colour_space": "RGBA"}),
        (migrate_clamp_v1_to_v2, {"channels": "CHANNEL_4", "include_alpha": True}),
        (migrate_invert_colour_v1_to_v2, {"invert_alpha": True}),
    ],
)
def test_v4_node_migrations_are_pure_and_rewrite_alpha_surfaces(
    migrate: Callable[[JsonObject], dict[str, JsonValue]],
    parameters: dict[str, object],
) -> None:
    source = {
        "id": ALPHA_CLAMP_ID,
        "type_id": "synmachine.image.clamp",
        "implementation_version": 1,
        "parameters": parameters,
    }
    original = deepcopy(source)

    migrated = migrate(source)  # type: ignore[arg-type]

    assert source == original
    assert migrated["implementation_version"] == 2
    if "target_colour_space" in parameters:
        assert migrated["parameters"] == {"target_colour_space": "SRGB"}
    elif "include_alpha" in parameters:
        assert migrated["parameters"] == {"channels": "COLOUR"}
    else:
        assert migrated["parameters"] == {}


@pytest.mark.parametrize(
    "type_id",
    [
        "synmachine.image.contrast",
        "synmachine.image.colour_levels",
        "synmachine.image.stretch_contrast",
        "synmachine.image.gamma",
        "synmachine.image.add_scalar",
        "synmachine.image.multiply_scalar",
        "synmachine.image.divide_scalar",
        # Filter nodes with the same channel-selection surface (ADR-0015
        # follow-up): Add Noise and Posterize.
        "synmachine.image.add_noise",
        "synmachine.image.posterize",
    ],
)
def test_v4_adjustment_selection_migration_is_pure_and_maps_channel_4(type_id: str) -> None:
    source = {
        "id": ALPHA_CLAMP_ID,
        "type_id": type_id,
        "implementation_version": 1,
        "parameters": {"channels": "CHANNEL_4"},
    }
    original = deepcopy(source)

    migrated = migrate_adjustment_channel_selection_v1_to_v2(source)  # type: ignore[arg-type]

    assert source == original
    assert migrated["implementation_version"] == 2
    assert migrated["parameters"] == {"channels": "COLOUR"}


def test_saved_v3_alpha_graph_migrates_and_compiles() -> None:
    payload = _alpha_v3_payload()

    snapshot = graph_from_json(json.dumps(payload), create_application_registry())

    by_type = {node.type_id: node for node in snapshot.nodes}
    assert "synmachine.image.opacity" not in by_type
    assert len(snapshot.nodes) == 7
    assert len(snapshot.connections) == 8

    parameters = {node.id: node.parameters for node in snapshot.nodes}
    combine = by_type["synmachine.image.combine_channels"]
    assert parameters[combine.id]["target_colour_space"] == "SRGB"
    ccs = by_type["synmachine.image.change_colour_space"]
    assert parameters[ccs.id]["target_colour_space"] == "SRGB"
    clamp = by_type["synmachine.image.clamp"]
    assert parameters[clamp.id]["channels"] == "COLOUR"
    assert "include_alpha" not in parameters[clamp.id]
    invert = by_type["synmachine.image.invert_colour"]
    assert parameters[invert.id] == {}
    bright = by_type["synmachine.image.brightness"]
    assert parameters[bright.id]["channels"] == "COLOUR"

    result = GraphCompiler(create_application_registry()).compile(snapshot)
    assert result.report.is_valid, [issue.message for issue in result.report.issues]


def test_v3_to_v4_graph_through_opacity_loads_invalid_not_unparseable() -> None:
    # Wiring a required input through the retired Opacity node must load (the
    # node and its connections are dropped) and surface as a normal
    # required-input validation error, never as a parse or unknown-port failure.
    payload = {
        "schema_version": 3,
        "application_version": "0.1.0",
        "document_id": ALPHA_DOC_ID,
        "nodes": [
            {
                "id": ALPHA_VIDEO_ID,
                "type_id": "synmachine.input.load_video",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"file_path": "media/reference.mp4"},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
            {
                "id": ALPHA_OPACITY_ID,
                "type_id": "synmachine.image.opacity",
                "implementation_version": 1,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {"factor": 0.5},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
            {
                "id": ALPHA_INVERT_ID,
                "type_id": "synmachine.visualization.display_image_data",
                "implementation_version": 2,
                "position": [0.0, 0.0],
                "size": None,
                "parameters": {},
                "ui_state": {},
                "user_label": None,
                "collapsed": False,
            },
        ],
        "connections": [
            {
                "id": "70000000-0000-0000-0000-000000000521",
                "source_node_id": ALPHA_VIDEO_ID,
                "source_port_id": "image",
                "destination_node_id": ALPHA_OPACITY_ID,
                "destination_port_id": "image",
                "ui_state": {},
            },
            {
                "id": "70000000-0000-0000-0000-000000000522",
                "source_node_id": ALPHA_OPACITY_ID,
                "source_port_id": "image",
                "destination_node_id": ALPHA_INVERT_ID,
                "destination_port_id": "image",
                "ui_state": {},
            },
        ],
        "groups": [],
        "document_settings": {},
        "ui_state": {},
    }

    snapshot = graph_from_json(json.dumps(payload), create_application_registry())

    assert {node.type_id for node in snapshot.nodes} == {
        "synmachine.input.load_video",
        "synmachine.visualization.display_image_data",
    }
    assert len(snapshot.connections) == 0

    result = GraphCompiler(create_application_registry()).compile(snapshot)
    assert not result.report.is_valid
    codes = {(issue.code, issue.port_id) for issue in result.report.errors}
    assert ("required_input_missing", "image") in codes
    assert all(code == "required_input_missing" for code, _ in codes)
