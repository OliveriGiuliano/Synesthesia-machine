# 16: Grow runtime_support into the image-node adapter home

**What to build:** The image-or-channel adapter machinery — IMAGE_OR_CHANNEL, the processor-runtime wrapper, _factory, _number, the combined validator, _channel_parameter, _as_image / _restore_type / _channel_like — is copy-pasted across filters.py, adjustments.py, and dimensions.py; one concept ('dynamic image/channel node') spans four files, and a filter-family rule change (e.g. the channel-override special case) must be re-derived per file.

**Solution:** Grow the 395-byte runtime_support.py into the shared adapter module: the image-or-channel runtime base, the factory/number helpers, validators, and the channel-bridge helpers; filters, adjustments, and dimensions import it and keep only their per-node algorithm and definition.

**Status:** open

**Files:**
- `src/synesthesia_machine/nodes/image/runtime_support.py`
- `src/synesthesia_machine/nodes/image/filters.py`
- `src/synesthesia_machine/nodes/image/adjustments.py`
- `src/synesthesia_machine/nodes/image/dimensions.py`

**Acceptance:**
- [ ] Dynamic image/channel node = one module
- [ ] New image node = smaller edit
- [ ] Adapter tests get one home
- [ ] Rule changes stop tripling
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
