# 14: Persistence facade slimming and store-owned recovery matching

**What to build:** The facade exports one-shot migration steps and schema TypedDicts that only fixture tests consume; the literal-JSON codec (ColorValue/NumericMatrix to/from JSON plus finite-number validation) is hand-rolled twice — graph_io.py and clipboard.py — with already-divergent error vocabularies; and MainWindow._explicit_path_for re-runs a full load_graph (parse + migrate + strict validation + media resolution) over every recent path just to compare a document_id the autosave manifest already records.

**Solution:** Keep the per-step functions in persistence.schemas (tests import them there directly) and drop them from the facade; factor the literal codec into one shared module used by both graph files and clipboard; extend AutosaveStore to match recovery records to documents via the manifest's document_id (keeping the path fallback only for pre-manifest records) so the UI stops re-parsing graphs at startup.

**Implemented:** The shared codec lives in `persistence/literals.py` and raises a single `LiteralDecodeError(kind, message, path)`; each consumer re-translates it into its own vocabulary (`GraphPersistenceError` for graph files, `ValueError` for clipboard text), so the message text is shared while each surface keeps its error type. The error-kind unification also collapses the old graph-side `missing_field`/`unknown_field` codes for malformed COLOR literal keys into `invalid_literal` (no test pinned the old codes). Recovery matching is store-owned: `AutosaveStore.explicit_path_for(record, recent_paths)` returns the manifest path for manifest-owned records and falls back to a lightweight `graph_document_id(path)` read (byte-bounded parse + migration + `document_id` field, no node/media validation) for pre-manifest records; `MainWindow._explicit_path_for` is deleted.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/persistence/__init__.py`
- `src/synesthesia_machine/persistence/schemas.py` (untouched; step functions stay here)
- `src/synesthesia_machine/persistence/literals.py` (new)
- `src/synesthesia_machine/persistence/graph_io.py`
- `src/synesthesia_machine/persistence/clipboard.py`
- `src/synesthesia_machine/persistence/autosave.py`
- `src/synesthesia_machine/persistence/README.md`
- `src/synesthesia_machine/ui/main_window.py`
- `tests/persistence/test_literals.py` (new)
- `tests/persistence/test_recovery.py`
- `tests/persistence/test_clipboard.py`
- `tests/persistence/test_migrations.py`

**Acceptance:**
- [x] Smaller interface, more depth
- [x] One codec implementation, two consumers
- [x] Startup recovery stops re-parsing graphs
- [x] New literal kind = one edit site
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
