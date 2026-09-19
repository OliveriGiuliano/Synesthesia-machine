# 18: One state-ordering policy for panic-capable sinks

**What to build:** ADR-0022 promises panic-capable sinks "inherit correct ordering for free", but only the stamping half of the protocol is central: `EngineFacade.tick` stamps `publish_generation` and the scheduler fans `panic(publish_generation)` to `PanicCapableRuntime`s, while the *rejection* half — "reject an ordered state at or below the most recent panic watermark" — is private watermark state plus a private rule, copied in `MidiOutputService.publish` (output_service.py:432-438) and `DebugSynth.update` (debug_synth.py:219-221). Worse, the two sinks disagree on the NoData path: `SendMidiRuntime` calls `request_panic(context.publish_generation)` with a latch, while `GenerateAudioRuntime.process` does `del context` and panics the audio service with the default generation 0 — setting **no** watermark — so an in-flight tick from another source (multi-source graphs are exactly ADR-0019's export scenario) can re-arm the synth's voices after the panic it just silenced: the precise race ADR-0022 exists to close, left open on one of the two panic-capable sinks and pinned by no test (nothing asserts what generation `GenerateAudioRuntime` passes on NoData). Every new panic-capable sink must rediscover the watermark rule, pick a NoData generation policy, and mirror `panic(publish_generation)` into its null variant — the exact per-sink cost ADR-0022's Context criticized in the old wall-clock protocol.

**Solution:** One small state-ordering policy module — reject a state ordered by the engine whose generation is at or below the watermark of the most recent panic; generation-0 states are never rejected — owned by the sinks and mirrored cheaply by their null variants; `GenerateAudioRuntime` panics with the tick's real `publish_generation` on NoData, like `SendMidiRuntime`. A future panic-capable sink adopts the policy instead of rediscovering it, making the ADR-0022 promise true.

**Status:** ready-for-agent

**Files:**
- `src/synesthesia_machine/midi/state_ordering.py` (new policy module, or beside `state_diff.py`)
- `src/synesthesia_machine/midi/output_service.py`
- `src/synesthesia_machine/midi/debug_synth.py`
- `src/synesthesia_machine/nodes/output/audio.py`
- `src/synesthesia_machine/nodes/output/midi.py`
- `tests/runtime/test_publish_generation.py`
- `tests/midi/test_output.py` · `tests/midi/test_debug_synth.py`

**Acceptance:**
- [ ] One policy module holds the rejection rule; both real sinks and both nulls apply it
- [ ] `GenerateAudioRuntime` panics with the tick's `publish_generation` on NoData
- [ ] Regression test: a stale state (generation below the panic watermark) is rejected by both sinks, driven with explicit generations — no clocks
- [ ] Null-variant conformance against the policy is checked, not assumed by construction
- [ ] `uv run check` green; MIDI + runtime test suites green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (MIDI-stack scout F1; ADR-0022's consequence "future panic-capable sinks inherit correct ordering for free" is currently false on the audio sink).
