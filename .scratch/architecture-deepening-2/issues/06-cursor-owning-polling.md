# 06: Cursor-owning preview polling

**What to build:** The EngineClient protocol's poll_*_previews(after_sequences) leaks the transport invariant (per-slot sequence numbers) to every consumer, forcing cursor state to be owned and advanced in two places (EngineSession engine_session.py:116-138, PreviewRouter preview_router.py:131-134) — even though both client implementations already retain the latest previews internally.

**Solution:** Replace the threshold API with next_*_previews() where each client owns its cursors; consumers call and forget, and the duplicated cursor dictionaries disappear from the session and the router.

**Blocked by:** 05

**Status:** open

**Files:**
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/runtime/engine_session.py`
- `src/synesthesia_machine/ui/preview_router.py`

**Acceptance:**
- [ ] Smaller protocol surface
- [ ] Cursor invariant localized in transports
- [ ] Two cursor dictionaries deleted
- [ ] Consumers can no longer poll wrongly
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
