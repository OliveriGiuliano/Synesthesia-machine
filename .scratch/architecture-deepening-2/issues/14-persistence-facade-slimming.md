# 14: Persistence facade slimming and store-owned recovery matching

**What to build:** The facade exports one-shot migration steps and schema TypedDicts that only fixture tests consume; the literal-JSON codec (ColorValue/NumericMatrix to/from JSON plus finite-number validation) is hand-rolled twice — graph_io.py and clipboard.py — with already-divergent error vocabularies; and MainWindow._explicit_path_for re-runs a full load_graph (parse + migrate + strict validation + media resolution) over every recent path just to compare a document_id the autosave manifest already records.

**Solution:** Keep the per-step functions in persistence.schemas (tests import them there directly) and drop them from the facade; factor the literal codec into one shared module used by both graph files and clipboard; extend AutosaveStore to match recovery records to documents via the manifest's document_id (keeping the path fallback only for pre-manifest records) so the UI stops re-parsing graphs at startup.

**Status:** open

**Files:**
- `src/synesthesia_machine/persistence/__init__.py`
- `src/synesthesia_machine/persistence/schemas.py`
- `src/synesthesia_machine/persistence/graph_io.py`
- `src/synesthesia_machine/persistence/clipboard.py`
- `src/synesthesia_machine/persistence/autosave.py`
- `src/synesthesia_machine/ui/main_window.py`

**Acceptance:**
- [ ] Smaller interface, more depth
- [ ] One codec implementation, two consumers
- [ ] Startup recovery stops re-parsing graphs
- [ ] New literal kind = one edit site
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
