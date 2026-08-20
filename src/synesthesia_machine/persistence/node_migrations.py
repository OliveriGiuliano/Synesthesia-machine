"""Pure, sequential migrations for persisted node implementation payloads."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

from synesthesia_machine.persistence.schemas import JsonObject, JsonValue

type NodeMigration = Callable[[JsonObject], JsonObject]
type NodeMigrationKey = tuple[str, int]


@dataclass(frozen=True, slots=True)
class NodeMigrationStep:
    type_id: str
    from_version: int
    to_version: int


@dataclass(frozen=True, slots=True)
class NodeMigrationResult:
    data: JsonObject
    steps: tuple[NodeMigrationStep, ...]


class NodeMigrationRegistry:
    """Map ``(type_id, from_version)`` to one deterministic migration step."""

    def __init__(
        self,
        migrations: Mapping[NodeMigrationKey, NodeMigration] | None = None,
    ) -> None:
        self._migrations = dict(migrations or {})

    def migrate(
        self,
        data: JsonObject,
        *,
        type_id: str,
        target_version: int,
    ) -> NodeMigrationResult:
        migrated = deepcopy(data)
        version = _implementation_version(migrated)
        if version > target_version:
            raise ValueError(
                f"{type_id!r} version {version} is newer than supported version {target_version}"
            )
        steps: list[NodeMigrationStep] = []
        while version < target_version:
            migration = self._migrations.get((type_id, version))
            if migration is None:
                raise ValueError(
                    f"No node migration is registered for {type_id!r} version {version}"
                )
            source_id = migrated.get("id")
            migrated_type = migrated.get("type_id")
            migrated = migration(deepcopy(migrated))
            next_version = _implementation_version(migrated)
            if next_version != version + 1:
                raise ValueError(
                    f"Node migration {type_id!r} version {version} must produce version "
                    f"{version + 1}"
                )
            if migrated.get("id") != source_id or migrated.get("type_id") != migrated_type:
                raise ValueError("Node migrations cannot change node identity or type")
            steps.append(NodeMigrationStep(type_id, version, next_version))
            version = next_version
        return NodeMigrationResult(migrated, tuple(steps))


def migrate_number_v0_to_v1(data: JsonObject) -> JsonObject:
    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    legacy_value = parameters.pop("value", None)
    if isinstance(legacy_value, int) and not isinstance(legacy_value, bool):
        parameters.setdefault("number_type", "INT")
        parameters.setdefault("int_value", legacy_value)
    elif isinstance(legacy_value, float):
        parameters.setdefault("number_type", "FLOAT")
        parameters.setdefault("float_value", legacy_value)
    elif "int_value" in parameters:
        parameters.setdefault("number_type", "INT")
    else:
        parameters.setdefault("number_type", "FLOAT")
    migrated["implementation_version"] = 1
    return migrated


def migrate_load_video_v0_to_v1(data: JsonObject) -> JsonObject:
    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    if "file_path" not in parameters and "path" in parameters:
        parameters["file_path"] = parameters.pop("path")
    migrated["implementation_version"] = 1
    return migrated


def migrate_hue_v1_to_v2(data: JsonObject) -> JsonObject:
    """Normalize legacy hue turns into the new single-turn literal range."""

    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    turns = parameters.get("turns")
    if isinstance(turns, float) and math.isfinite(turns) and not 0.0 <= turns <= 1.0:
        parameters["turns"] = turns % 1.0
    migrated["implementation_version"] = 2
    return migrated


def migrate_display_image_data_v1_to_v2(data: JsonObject) -> JsonObject:
    """Drop the preview cadence/cap params now fixed in the preview broker."""

    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    parameters.pop("preview_fps", None)
    parameters.pop("max_dimension", None)
    migrated["implementation_version"] = 2
    return migrated


def migrate_channel_display_v1_to_v2(data: JsonObject) -> JsonObject:
    """Drop the preview cadence/cap params now fixed in the preview broker."""

    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    parameters.pop("preview_fps", None)
    parameters.pop("max_dimension", None)
    migrated["implementation_version"] = 2
    return migrated


def _parameters(data: JsonObject) -> JsonObject:
    raw_parameters = data.get("parameters")
    if not isinstance(raw_parameters, dict):
        raise ValueError("Node parameters must be an object before migration")
    return cast(dict[str, JsonValue], raw_parameters)


def _implementation_version(data: JsonObject) -> int:
    value = data.get("implementation_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Node implementation_version must be a non-negative integer")
    return value


BUILTIN_NODE_MIGRATIONS = NodeMigrationRegistry(
    {
        ("synmachine.image.hue", 1): migrate_hue_v1_to_v2,
        ("synmachine.input.load_video", 0): migrate_load_video_v0_to_v1,
        ("synmachine.utility.number", 0): migrate_number_v0_to_v1,
        ("synmachine.visualization.channel_display", 1): migrate_channel_display_v1_to_v2,
        ("synmachine.visualization.display_image_data", 1): migrate_display_image_data_v1_to_v2,
    }
)


__all__ = [
    "BUILTIN_NODE_MIGRATIONS",
    "NodeMigration",
    "NodeMigrationRegistry",
    "NodeMigrationResult",
    "NodeMigrationStep",
    "migrate_channel_display_v1_to_v2",
    "migrate_display_image_data_v1_to_v2",
    "migrate_hue_v1_to_v2",
    "migrate_load_video_v0_to_v1",
    "migrate_number_v0_to_v1",
]
