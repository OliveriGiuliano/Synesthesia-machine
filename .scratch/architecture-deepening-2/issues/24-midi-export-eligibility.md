# 24: Move MIDI export eligibility next to the headless exporter

**What to build:** midi_export_eligibility re-implements 'what a graph may contain for export' from node internals — the file_path parameter id plus four hard-coded type IDs — and mirrors the headless exporter's failure codes; the UI then caches the scan in the window. ADR-0016/0019 define these rules, but the implementation lives next to the dialog, not next to run_midi_export.

**Solution:** A Qt-free midi_export_eligibility(snapshot, registry) -> (eligible, reason) beside run_midi_export in the runtime package, sharing the stable reason codes of MidiExportError; the UI keeps only the reason-to-text mapping and the dialog/overlay.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/ui/midi_export.py`
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/ui/main_window.py`


## Answer

- [x] One Qt-free midi_export_eligibility(snapshot) -> (eligible, reason) beside run_midi_export in the runtime package, sharing the stable reason codes of MidiExportError; the UI keeps only the reason-to-text mapping and the dialog/overlay.
- [x] Reason codes mirror the exporter's failure codes exactly; the menu state, export, and tests share one scan.
- [x] Testable in tests/runtime without Qt; a parity test pins menu state to exporter codes.
- [x] Targeted tests pass: uv run check green; no unrelated diff.

- Files:
  - src/synesthesia_machine/runtime/midi_export.py (new _scan_export_nodes + midi_export_eligibility; _validate_export_inputs refactored onto the shared scan)
  - src/synesthesia_machine/runtime/__init__.py (facade export)
  - src/synchestra_machine/ui/midi_export.py (eligibility removed; unsupported_source tooltip added)
  - src/synchestra_machine/ui/main_window.py (window calls the runtime version)
  - tests/runtime/test_midi_export.py (headless eligibility + parity tests)
  - tests/ui/test_midi_export_dialog.py (import moved to runtime)

Commit: 2b00777 "Give MIDI export eligibility a headless home beside the exporter"

Note: the UI's old order preferred problem kinds (camera before missing media) while the shared scan reports the first problem in node order; the disabled/enabled outcome is identical, only the tooltip text can differ on graphs with multiple distinct source problems.
- [ ] Rules live next to the code that enforces them
- [ ] Menu state, export, and tests share one scan
- [ ] Testable in tests/runtime without Qt
- [ ] New export rule = headless edit only
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
