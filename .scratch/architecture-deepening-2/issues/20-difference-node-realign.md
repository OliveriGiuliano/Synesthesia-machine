# 20: Realign the Difference node with its domain

**What to build:** synmachine.image.difference — an image-domain type_id in the 'Image / Compositing' category — is owned by nodes/utility/dynamic.py (a 794-line grab-bag of six unrelated nodes) and re-placed into the image catalogue by a cross-domain import plus a positional reorder hack (*temporal[1:], *temporal[:1] to push hold_image last). The type_id prefix and the file disagree about where the node lives.

**Solution:** Move the Difference definition and runtime into the image domain (compositing), split dynamic.py along the subcategories its own definitions already declare (temporal / analysis / transform), and let the catalogue ordering fall out of declaration order instead of the positional swap.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/nodes/utility/dynamic.py`
- `src/synesthesia_machine/nodes/image/catalogue.py`
- `tests/architecture/test_builtin_catalogue.py`

**Acceptance:**
- [x] Type_id prefix matches file location
- [x] Catalogue stops crossing domains
- [x] The reorder hack is deleted
- [x] Dynamic.py stops being a grab-bag
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
