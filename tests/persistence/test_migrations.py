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
    migrate_channel_display_v1_to_v2,
    migrate_display_image_data_v1_to_v2,
    migrate_hue_v1_to_v2,
    migrate_statistics_v1_to_v2,
    migrate_v2_to_v3,
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
    assert legacy.graph_migration_steps == 3
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
