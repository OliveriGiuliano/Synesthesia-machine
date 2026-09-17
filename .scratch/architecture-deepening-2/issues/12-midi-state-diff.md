# 12: One shared MIDI state-diff primitive

**What to build:** The rule 'compare desired note states, emit only deltas' is implemented three times with divergent semantics: output_service._reconcile (velocity policies, off-before-on), debug_synth._reconcile (voices, stealing, panic), and midi_export._diff_to_events (on/off only, silently dropping velocity updates). The export's on/off-only policy — the actual intent of ADR-0016/0019 — is an accidental property of its third implementation, invisible at the call site.

**Solution:** A pure diff_midi_states(previous, current) -> (note_offs, note_ons_with_velocity) primitive in the midi package; the output service layers its velocity policies on top, the export explicitly projects the diff to on/off (making the ADR policy a declared choice), and the debug synth reuses the add/remove diff for voice entry/exit.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/midi/state_diff.py (new)`
- `src/synesthesia_machine/midi/output_service.py`
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/midi/debug_synth.py`

**Acceptance:**
- [x] One diff, three callers
- [x] Export policy declared, not accidental
- [x] Velocity semantics change in one file
- [x] AGENTS.md rule stays true: only the output service emits wire messages
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
