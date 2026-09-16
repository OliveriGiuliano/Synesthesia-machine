# 09: Shared presented source-tick builder

**What to build:** VideoSourceService and CameraSourceService each independently build the same presented-tick invariants: uint8 to float32/255 read-only normalization (_convert_frame vs _bgr_to_rgb_float32), FrameContext construction, SRGB + (R,G,B) + AlphaMode.NONE frame shaping, and index bookkeeping. A new runtime-data invariant must land in both services in lockstep, and a bug in one copy stays local to that source.

**Solution:** One small pure module (e.g. media/source_frame.py) exposes a presented-tick builder that owns the conversion, context construction, and index rules; both services call it, and the builder is directly unit-testable without any thread or device.

**Status:** open

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/media/camera_source.py`
- `src/synesthesia_machine/media/source_frame.py (new)`

**Acceptance:**
- [ ] ImageFrame invariants live in one place
- [ ] Builder is pure: testable without threads
- [ ] A third source kind gets the rules for free
- [ ] Per-source drift becomes impossible
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
