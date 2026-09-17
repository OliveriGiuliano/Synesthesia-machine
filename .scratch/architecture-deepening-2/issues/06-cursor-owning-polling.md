# 06: Cursor-owning preview polling

**What to build:** The EngineClient protocol's poll_*_previews(after_sequences) leaks the transport invariant (per-slot sequence numbers) to every consumer, forcing cursor state to be owned and advanced in two places (EngineSession engine_session.py:116-138, PreviewRouter preview_router.py:131-134) — even though both client implementations already retain the latest previews internally.

**Solution:** Replace the threshold API with next_*_previews() where each client owns its cursors; consumers call and forget, and the duplicated cursor dictionaries disappear from the session and the router.

**Blocked by:** 05

**Status:** resolved

**Files:**
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/runtime/engine_session.py`
- `src/synesthesia_machine/ui/preview_router.py`

**Acceptance:**
- [x] Smaller protocol surface
- [x] Cursor invariant localized in transports
- [x] Two cursor dictionaries deleted
- [x] Consumers can no longer poll wrongly
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
- [x] Fresh-context review findings fixed: in-process cursor reset on
  `activate()` (child publisher path), window no longer closes client preview
  state (widgets only), process-client image cursors lock-guarded
