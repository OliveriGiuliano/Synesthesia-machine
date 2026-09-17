# 16: Grow runtime_support into the image-node adapter home

**What to build:** The image-or-channel adapter machinery — IMAGE_OR_CHANNEL, the processor-runtime wrapper, _factory, _number, the combined validator, _channel_parameter, _as_image / _restore_type / _channel_like — is copy-pasted across filters.py, adjustments.py, and dimensions.py; one concept ('dynamic image/channel node') spans four files, and a filter-family rule change (e.g. the channel-override special case) must be re-derived per file.

**Solution:** Grow the 395-byte runtime_support.py into the shared adapter module: the image-or-channel runtime base, the factory/number helpers, validators, and the channel-bridge helpers; filters, adjustments, and dimensions import it and keep only their per-node algorithm and definition.

**Status:** resolved

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

# Answer

Shipped: `runtime_support.py` is now the single home of the image-or-channel adapter machinery — `IMAGE_OR_CHANNEL`, the `FilterProcessor`/`AdjustmentProcessor` aliases, `FilterRuntime`/`AdjustmentRuntime`, `image_source`, `as_colour_image`/`as_value_image`, `restore_frame_type`, `channel_like`, `dynamic_number`, `channel_selection_parameter`, `combined_parameter_validator`, and `dynamic_image_channel_resolver`. filters.py, adjustments.py, and dimensions.py import it and keep only their per-node algorithms and definitions (net −366 lines across the three families). The `AdjustmentProcessor` alias keeps its intentionally loose `Callable[..., ImageSource]` form so mixed image/channel processor signatures stay type-compatible. New test home `tests/nodes/image/test_runtime_support.py` (14 tests) covers the adapters, both runtimes, and the shared validators.
