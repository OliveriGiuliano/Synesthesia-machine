# 13: Give desired MIDI state its own ordering

**What to build:** DebugSynth rejects stale states by wall-clock comparison of state.context.received_monotonic_ns against its panic timestamp — a cross-thread ordering protocol embedded in a renderer, which is exactly where the 22b351b stale-state fix had to land. MidiStateFrame carries no ordering of its own, so every future sink (hardware synth, export) must rediscover the panic/state race against the wall clock.

**Solution:** Record a new ADR first, then carry a publish generation (or a panic-generation stamp applied at the publish seam) on the desired-state value so stale rejection becomes a generation comparison owned by the state model; the debug synth compares generations, no clocks.

**ADR:** Recorded first, per the architecture delta gate: `docs/adr/0022-publish-generation-panic-ordering.md`. Research showed `FrameContext`/`MidiStateFrame` are engine-local (no wire payload carries a frame context), so `ENGINE_PROTOCOL_VERSION` did not change.

**Blocked by:** 12

**Status:** resolved

**Files:**
- `docs/adr/0022-publish-generation-panic-ordering.md (new)`
- `src/synesthesia_machine/contracts/runtime_values.py`
- `src/synesthesia_machine/runtime/engine_facade.py`
- `src/synesthesia_machine/runtime/scheduler.py`
- `src/synesthesia_machine/nodes/base.py`
- `src/synesthesia_machine/nodes/output/audio.py`
- `src/synesthesia_machine/nodes/output/midi.py`
- `src/synesthesia_machine/midi/debug_synth.py`
- `src/synesthesia_machine/midi/output_service.py`
- tests: migrated fakes in `tests/midi/test_debug_synth.py`, `tests/midi/test_output.py`, `tests/integration/test_video_to_midi.py`; new tests `tests/midi/test_debug_synth.py`, `tests/midi/test_output.py`, `tests/runtime/test_publish_generation.py (new)`

**Acceptance:**
- [x] Ordering rule lives in the value, not the renderer
- [x] Deterministic: no wall clocks in tests
- [x] Future sinks inherit correct ordering for free
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
