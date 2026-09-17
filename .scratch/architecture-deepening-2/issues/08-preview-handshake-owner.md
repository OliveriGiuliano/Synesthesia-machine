# 08: One owner for the preview-slot handshake

**What to build:** The preview-slot state machine (announce / attach / ready / re-announce / clear-on-activation) is discoverable only by reading four modules that share a bare writer object: the writer's state machine in preview_channel.py, the wake loop in _EventPublisher, the UI-side slot configuration in ProcessEngineClient, and the broker's generation clearing — and one activation transaction clears the shared writer twice from two modules that do not reference each other. The PreviewTransport protocol is partly fictional: reader/writer implement half-noop methods while the publisher calls concrete writer methods that are not in the protocol.

**Solution:** Concentrate the whole slot lifecycle — including the activation clear — in the SharedMemoryPreviewWriter (preview_channel.py); reduce _EventPublisher to a dumb wake/drain loop; make the single clear site explicit; and trim the PreviewTransport protocol to the methods adapters actually implement.

**Blocked by:** 07

**Status:** resolved

**Files:**
- `src/synesthesia_machine/runtime/preview_channel.py`
- `src/synesthesia_machine/runtime/engine_server.py`
- `src/synesthesia_machine/runtime/previews.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`

**Acceptance:**
- [x] ADR-0009 generation stamping lives in one file
- [x] Activation clears the writer exactly once
- [x] Protocol methods match real adapter behaviour
- [x] Double-clear race surface disappears
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
