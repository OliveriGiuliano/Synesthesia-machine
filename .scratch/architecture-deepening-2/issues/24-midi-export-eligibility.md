# 24: Move MIDI export eligibility next to the headless exporter

**What to build:** midi_export_eligibility re-implements 'what a graph may contain for export' from node internals — the file_path parameter id plus four hard-coded type IDs — and mirrors the headless exporter's failure codes; the UI then caches the scan in the window. ADR-0016/0019 define these rules, but the implementation lives next to the dialog, not next to run_midi_export.

**Solution:** A Qt-free midi_export_eligibility(snapshot, registry) -> (eligible, reason) beside run_midi_export in the runtime package, sharing the stable reason codes of MidiExportError; the UI keeps only the reason-to-text mapping and the dialog/overlay.

**Status:** open

**Files:**
- `src/synesthesia_machine/ui/midi_export.py`
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/ui/main_window.py`

**Acceptance:**
- [ ] Rules live next to the code that enforces them
- [ ] Menu state, export, and tests share one scan
- [ ] Testable in tests/runtime without Qt
- [ ] New export rule = headless edit only
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
