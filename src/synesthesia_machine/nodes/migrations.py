"""Node-implementation-version migrations owned by node definitions.

Each node definition declares the chain of migrations that raise a saved node
payload from an older implementation version to the current one. ``migrate_node_data``
walks that chain; persistence asks a definition for its migrations rather than
consulting a separately hard-coded table. The functions here operate on the shared
``JsonObject`` value type (from ``contracts``) so they live in the headless
``nodes`` layer, which ``persistence`` may depend on but not vice-versa.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

from synesthesia_machine.contracts import JsonObject, JsonValue

type NodeMigration = Callable[[JsonObject], JsonObject]


@dataclass(frozen=True, slots=True)
class NodeMigrationStep:
    type_id: str
    from_version: int
    to_version: int


@dataclass(frozen=True, slots=True)
class NodeMigrationResult:
    data: JsonObject
    steps: tuple[NodeMigrationStep, ...]


def migrate_node_data(
    type_id: str,
    migrations: Mapping[int, NodeMigration],
    data: JsonObject,
    *,
    target_version: int,
) -> NodeMigrationResult:
    """Chain ``migrations`` from the saved version up to ``target_version``.

    Mirrors the historical hard-coded registry: each step must raise the
    ``implementation_version`` by exactly one and preserve the node ``id`` and
    ``type_id``. A version newer than ``target_version`` is an error, as is a
    missing step for an older version.
    """
    migrated = deepcopy(data)
    version = _implementation_version(migrated)
    if version > target_version:
        raise ValueError(
            f"{type_id!r} version {version} is newer than supported version {target_version}"
        )
    steps: list[NodeMigrationStep] = []
    while version < target_version:
        migration = migrations.get(version)
        if migration is None:
            raise ValueError(f"No node migration is registered for {type_id!r} version {version}")
        source_id = migrated.get("id")
        migrated_type = migrated.get("type_id")
        migrated = migration(deepcopy(migrated))
        next_version = _implementation_version(migrated)
        if next_version != version + 1:
            raise ValueError(
                f"Node migration {type_id!r} version {version} must produce version {version + 1}"
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


def migrate_load_video_v1_to_v2(data: JsonObject) -> JsonObject:
    """Load Video v2 adds the optional loop start/end timestamps (seconds)."""
    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    parameters.setdefault("loop_start_s", 0.0)
    parameters.setdefault("loop_end_s", 0.0)
    migrated["implementation_version"] = 2
    return migrated


def migrate_hue_v1_to_v2(data: JsonObject) -> JsonObject:
    """Normalize legacy hue turns into the new single-turn literal range."""

    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    turns = parameters.get("turns")
    if not isinstance(turns, bool) and isinstance(turns, (int, float)):
        # Legacy turns may be an int literal, but the v2 turns parameter is a
        # strict float; normalise to float so it passes validation, and wrap
        # finite out-of-range values into the single-turn range so the user's
        # hue is not silently lost or rejected.
        turns_value = float(turns)
        if math.isfinite(turns_value) and not 0.0 <= turns_value <= 1.0:
            turns_value = turns_value % 1.0
        parameters["turns"] = turns_value
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


def migrate_statistics_v1_to_v2(data: JsonObject) -> JsonObject:
    """Statistics v2 replaced its single ``values`` input with variadic sockets.

    No parameter payload changed; the input-port rename lives in the v2 -> v3
    graph migration, so this step only advances the implementation version.
    """

    migrated = deepcopy(data)
    migrated["implementation_version"] = 2
    return migrated


def migrate_separate_channels_v1_to_v2(data: JsonObject) -> JsonObject:
    """Separate Channels v2 dropped the unreachable fourth (alpha) output.

    Sources never carry an alpha channel; the ``channel_4`` socket removal
    and any saved connections to it live in the v3 -> v4 graph migration.
    """

    migrated = deepcopy(data)
    migrated["implementation_version"] = 2
    return migrated


def _rewritten_target(data: JsonObject) -> JsonObject:
    """Rewrite the retired RGBA target to the SRGB default."""
    parameters = _parameters(data)
    if parameters.get("target_colour_space") == "RGBA":
        parameters["target_colour_space"] = "SRGB"
    return data


def migrate_combine_channels_v1_to_v2(data: JsonObject) -> JsonObject:
    """Combine Channels v2 dropped the fourth input and the RGBA target."""

    migrated = deepcopy(data)
    _rewritten_target(migrated)
    migrated["implementation_version"] = 2
    return migrated


def migrate_change_colour_space_v1_to_v2(data: JsonObject) -> JsonObject:
    """Change Colour Space v2 no longer offers RGBA as a target."""

    migrated = deepcopy(data)
    _rewritten_target(migrated)
    migrated["implementation_version"] = 2
    return migrated


def migrate_invert_colour_v1_to_v2(data: JsonObject) -> JsonObject:
    """Invert Colour v2 dropped the alpha-inversion parameter."""

    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    parameters.pop("invert_alpha", None)
    migrated["implementation_version"] = 2
    return migrated


def migrate_clamp_v1_to_v2(data: JsonObject) -> JsonObject:
    """Clamp v2 dropped the alpha parameters and the CHANNEL_4 target."""

    migrated = deepcopy(data)
    parameters = _parameters(migrated)
    parameters.pop("include_alpha", None)
    _rewrite_channel_selection(migrated)
    migrated["implementation_version"] = 2
    return migrated


def _rewrite_channel_selection(data: JsonObject) -> None:
    """Rewrite the retired CHANNEL_4 selection to the COLOUR default."""
    parameters = _parameters(data)
    if parameters.get("channels") == "CHANNEL_4":
        parameters["channels"] = "COLOUR"


def migrate_adjustment_channel_selection_v1_to_v2(data: JsonObject) -> JsonObject:
    """Channel-selection v2 dropped the unreachable CHANNEL_4 target."""

    migrated = deepcopy(data)
    _rewrite_channel_selection(migrated)
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


__all__ = [
    "NodeMigration",
    "NodeMigrationResult",
    "NodeMigrationStep",
    "migrate_adjustment_channel_selection_v1_to_v2",
    "migrate_change_colour_space_v1_to_v2",
    "migrate_channel_display_v1_to_v2",
    "migrate_clamp_v1_to_v2",
    "migrate_combine_channels_v1_to_v2",
    "migrate_display_image_data_v1_to_v2",
    "migrate_hue_v1_to_v2",
    "migrate_invert_colour_v1_to_v2",
    "migrate_load_video_v0_to_v1",
    "migrate_load_video_v1_to_v2",
    "migrate_node_data",
    "migrate_number_v0_to_v1",
    "migrate_separate_channels_v1_to_v2",
    "migrate_statistics_v1_to_v2",
]
