# 17: Nodes seam and facade hygiene

**What to build:** Eight import sites outside the package bypass the nodes facade into nodes.base / nodes.registry (violating master section 17.2's public-module rule), so base.py is a frozen internal path that a future reorganization would break; media/video_source.py and media/camera_source.py import ResetReason from nodes even though its true owner is contracts.engine_client; and FourierRuntime exposes a public .cache attribute that tests assert on directly (test_fourier.py:242-312), bypassing the capability-protocol pattern the rest of the scheduler seam uses.

**Solution:** Import through the synesthesia_machine.nodes facade everywhere (media takes ResetReason from contracts); make the Fourier shape cache private with behaviour-level tests through process outputs (or a small introspection protocol if a consumer ever needs it); and let the boundary tests enforce the facade rule so the drift cannot return.

**Status:** open

**Files:**
- `src/synesthesia_machine/graph/compiler.py`
- `src/synesthesia_machine/runtime/execution_plan.py`
- `src/synesthesia_machine/runtime/scheduler.py`
- `src/synesthesia_machine/runtime/engine_facade.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/runtime/midi_export.py`
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/media/camera_source.py`
- `src/synesthesia_machine/nodes/synesthesia/fourier.py`
- `tests/nodes/synesthesia/test_fourier.py`

**Acceptance:**
- [ ] Facade is the true interface surface
- [ ] Base.py becomes a movable module
- [ ] Seam stays protocol-only
- [ ] Media no longer depends on nodes at all
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
