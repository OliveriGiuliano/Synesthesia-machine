"""Node-implementation-version migrations owned by node definitions.

The central module owns the walker and its value types. Each per-node step
function lives in the module that owns the ``NodeDefinition`` it is attached
to, so a node's payload change and its migration read together; ``migrations``
holds only the shared payload helper ``migration_parameters``.
"""

from __future__ import annotations

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


def migration_parameters(data: JsonObject) -> JsonObject:
    """Return the saved node's parameter object, validated for migration."""
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
    "migrate_node_data",
    "migration_parameters",
]
