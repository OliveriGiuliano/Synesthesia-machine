# 13: Give desired MIDI state its own ordering

**What to build:** DebugSynth rejects stale states by wall-clock comparison of state.context.received_monotonic_ns against its panic timestamp — a cross-thread ordering protocol embedded in a renderer, which is exactly where the 22b351b stale-state fix had to land. MidiStateFrame carries no ordering of its own, so every future sink (hardware synth, export) must rediscover the panic/state race against the wall clock.

**Solution:** Record a new ADR first, then carry a publish generation (or a panic-generation stamp applied at the publish seam) on the desired-state value so stale rejection becomes a generation comparison owned by the state model; the debug synth compares generations, no clocks.

**ADR:** Requires a new ADR before implementation: a versioned contracts value crosses the process boundary (ADR-0005 territory); AGENTS.md treats this as an architecture-level contract change.

**Blocked by:** 12

**Status:** open

**Files:**
- `docs/adr/ (new ADR)`
- `src/synesthesia_machine/contracts/runtime_values.py`
- `src/synesthesia_machine/midi/debug_synth.py`
- `src/synesthesia_machine/nodes/output/audio.py`

**Acceptance:**
- [ ] Ordering rule lives in the value, not the renderer
- [ ] Deterministic: no wall clocks in tests
- [ ] Future sinks inherit correct ordering for free
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
