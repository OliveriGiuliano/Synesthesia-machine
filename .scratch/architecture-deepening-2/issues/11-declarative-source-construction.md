# 11: Declarative source construction and media references

**What to build:** What a source node is — which parameter carries the media file, which ports it emits, how its service is constructed — is re-encoded in four places: persistence hardcodes LOAD_VIDEO_TYPE_ID + the file_path parameter id in both graph_io.py and media_relink.py (and relinked_media_node is missing from the facade, so the UI imports the submodule directly), the engine factory enumerates every source-config field plus parallel raw parameter reads, and the graph worker hardcodes the source output port names 'image' / 'processed_index' — a new source node with different names would still 'run' but its outputs would be silently orphaned.

**Solution:** Source node definitions declare their media-reference parameter, their output-port contract, and their source-config builder; persistence asks the registry instead of special-casing, the engine factory builds sources from the declared config, the worker reads declared output ports (validating against the node's own definition), and relinked_media_node moves to the facade.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/input/video.py`
- `src/synesthesia_machine/nodes/input/camera.py`
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/persistence/graph_io.py`
- `src/synesthesia_machine/persistence/media_relink.py`
- `src/synesthesia_machine/persistence/__init__.py`

**Acceptance:**
- [x] A new source = declare, not four-module hunt
- [x] Port-name contract shared by worker and definitions
- [x] Media-reference knowledge lives with the node
- [x] Facade discipline restored (ui import fixed)
**Plan:**
- `nodes/base.py`: new `SourceOutputContract` (declares the port ids a source's published frame maps to, validated against the definition's outputs); `NodeDefinition` gains optional `media_parameter_id` (string parameter carrying the media file reference), `source_outputs`, and `source_config_builder` (parameters → typed source config).
- `nodes/input/video.py` / `camera.py`: declare the contract (video: `file_path` media parameter; camera: none) and the existing `build_*_source_config` builders.
- `persistence/media_relink.py`: `media_parameter_id(type_id)` lookup derived from the input definitions' declarations; `find_missing_media` / `relinked_media_node` use it instead of hardcoding LOAD_VIDEO + "file_path". `graph_io.py` media-path helpers likewise; `relinked_media_node` exported from the `persistence` facade; `ui/commands/graph_commands.py` imports from the facade.
- `runtime/in_process_engine.py`: factory protocols take the typed config object (no field re-enumeration, no parallel raw parameter reads); default factories map config → service constructor; `_create_source` is builder-driven (a source without a declared builder fails loudly instead of falling through to video); `_reusable_source_ids` tests `execution_kind is SOURCE`; the graph worker resolves each tick's port keys from the active plan's declared `source_outputs` (a source without a contract fails loudly instead of orphaning outputs).
- `runtime/midi_export.py`: `_ExportVideoSourceFactory` takes the config object.
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
