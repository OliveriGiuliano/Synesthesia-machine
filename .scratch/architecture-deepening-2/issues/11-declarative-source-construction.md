# 11: Declarative source construction and media references

**What to build:** What a source node is — which parameter carries the media file, which ports it emits, how its service is constructed — is re-encoded in four places: persistence hardcodes LOAD_VIDEO_TYPE_ID + the file_path parameter id in both graph_io.py and media_relink.py (and relinked_media_node is missing from the facade, so the UI imports the submodule directly), the engine factory enumerates every source-config field plus parallel raw parameter reads, and the graph worker hardcodes the source output port names 'image' / 'processed_index' — a new source node with different names would still 'run' but its outputs would be silently orphaned.

**Solution:** Source node definitions declare their media-reference parameter, their output-port contract, and their source-config builder; persistence asks the registry instead of special-casing, the engine factory builds sources from the declared config, the worker reads declared output ports (validating against the node's own definition), and relinked_media_node moves to the facade.

**Status:** open

**Files:**
- `src/synesthesia_machine/nodes/input/video.py`
- `src/synesthesia_machine/nodes/input/camera.py`
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/persistence/graph_io.py`
- `src/synesthesia_machine/persistence/media_relink.py`
- `src/synesthesia_machine/persistence/__init__.py`

**Acceptance:**
- [ ] A new source = declare, not four-module hunt
- [ ] Port-name contract shared by worker and definitions
- [ ] Media-reference knowledge lives with the node
- [ ] Facade discipline restored (ui import fixed)
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
