# 06: Own the migration chain in NodeDefinition (expand step)

## What changed

A node definition now owns its implementation-version migration chain, so persistence migrates
a saved node by asking its definition instead of consulting the separately hard-coded
`BUILTIN_NODE_MIGRATIONS` table.

- **JSON value types to `contracts`.** `JsonObject`/`JsonValue` moved from
  `persistence/schemas.py` to `contracts/json.py` and are exported from the `contracts`
  facade. Both `nodes` and `persistence` reference the same objects; `schemas.py` re-exports
  them so existing `persistence.schemas` importers are unchanged. This is what lets the
  headless `nodes` layer own migrations without depending on `persistence`.
- **Migrations to `nodes`.** The twelve `migrate_*` functions, the `NodeMigration`/
  `NodeMigrationStep`/`NodeMigrationResult` types, and a new `migrate_node_data` chain driver
  moved from `persistence/node_migrations.py` to `nodes/migrations.py` (exported from the
  `nodes` facade). `migrate_node_data(type_id, migrations, data, *, target_version)` walks a
  definition's `from_version -> step` mapping, applying each step and validating that every step
  raises `implementation_version` by exactly one and preserves the node `id`/`type_id` — the
  same invariants the old registry enforced.
- **`NodeDefinition.migrations`.** The definition carries
  `migrations: Mapping[int, NodeMigration]` (default an empty, per-instance mapping). Each node
  with an older saved version declares its chain: the image adjustment family (brightness,
  contrast, colour_levels, divide_scalar, gamma, multiply_scalar, stretch_contrast, add_scalar,
  add_noise, posterize) -> `migrate_adjustment_channel_selection_v1_to_v2`; clamp -> clamp; hue
  -> hue; invert_colour -> invert_colour; change_colour_space, combine_channels,
  separate_channels -> their own steps; load_video + number migrate from v0; statistics,
  channel_display, display_image_data from v1.
- **`graph_io` routes through the definition.** `_node_from_data` now calls
  `migrate_node_data(type_id, definition.migrations, data, target_version=...)` instead of
  `BUILTIN_NODE_MIGRATIONS.migrate(...)`.
- **Parallel path retained.** `persistence/node_migrations.py` keeps `NodeMigrationRegistry` +
  `BUILTIN_NODE_MIGRATIONS` (now importing the functions/types from `nodes`); the registry's
  `migrate` delegates to `migrate_node_data` so the two paths cannot drift. This module is
  retired in the contract step (ticket 12).

## Verification

- `uv run check` passes (ruff format, ruff, strict Pyright). The `migrations` default is a
  typed `dict[int, NodeMigration]` factory so the field is not partially unknown.
- `uv run pytest -q` — 1152 passed, 3 skipped. The compatibility migration fixtures
  (`tests/persistence/test_migrations.py`) load + migrate through the new definition-owned path
  and produce the same results as the legacy table; `graph_io` round-trips unchanged.
- A registry probe confirms every mapped type_id exposes exactly its declared chain and every
  unmapped node holds a genuine empty mapping (distinct per instance).

## Note for the contract step (ticket 12)

Once `graph_io` is the only consumer, `BUILTIN_NODE_MIGRATIONS` + `NodeMigrationRegistry` (and
their re-exports in the `persistence` facade) can be deleted; the definition-owned
`migrate_node_data` + `NodeDefinition.migrations` become the single source of truth.
