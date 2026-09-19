# 01: Shrink the engine driver to the depth of its real behaviours

**What to build:** `EngineSession` re-exposes the client protocol's 26 methods, but 20 of its 28 public methods are single-line forwards; its genuinely new behaviours are four (the known-stopped cache, the folded six-state connection machine, the restart-outcome mapping, the preview pump). The fold re-derives, from `status.last_heartbeat_monotonic_ns`, exactly what `ProcessEngineClient._refresh_liveness_locked` already computes with the same `DEFAULT_HEARTBEAT_TIMEOUT_S` — redundant over the process adapter, and over the in-process adapter (whose `status()` never sets a heartbeat timestamp) the fold can never fire. The session also keeps a fourth copy of the "last valid plan" memory (the process client, the in-process client, and the never-read child-side copy already keep their own), and its restart-fallback rebuild — unreachable for both real adapters — would use `ResetReason.PLAN_REPLACED` where both clients' own restart paths use `ENGINE_RESTARTED`, a latent state-retention divergence in dead code. Finally, `clear_previews` is in the protocol and implemented by both clients but absent from the session, so the driver's surface disagrees with the protocol it wraps.

**Solution:** The session keeps exactly its four real behaviours behind a small interface; plan-restart memory is owned by the adapters (one owner per transport, behind the shared protocol) and the session's copy plus the dead fallback are deleted; the connection fold exists once (in the process adapter, where liveness is real) rather than twice with one copy dead; the session's exposed surface is reconciled with the protocol (expose `clear_previews` or settle which layer owns it) so a reader of either file sees the same truth.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/runtime/engine_session.py`
- `src/synesthesia_machine/contracts/engine_client.py`
- `src/synesthesia_machine/runtime/engine_client.py`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/ui/engine_bridge.py`
- `tests/runtime/test_engine_session.py`

**Acceptance:**
- [ ] The session's public surface is its real behaviours; no protocol method is re-implemented as a forward the reader must verify against the protocol
- [ ] Exactly one owner of the last-valid-plan memory per transport; the unreachable session fallback is deleted
- [ ] The connection fold exists once and is not dead over the in-process adapter
- [ ] A user-initiated restart maps to one reset policy no matter which layer rebuilds the plan
- [ ] Protocol and driver surfaces agree (a test pins the match)
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff

## Comments

- 2026-09-18: From the third architecture-review run (report /tmp/architecture-review-20260918-193020.html, candidate "Shrink the engine driver to the depth of its real behaviours"; engine-cluster scout).
- 2026-09-18: Implemented. The session surface is now its five genuine behaviours (preview pump with closed-guarded per-family cursor resets, the known-stopped cache, the restart-outcome mapping, the demand-roots callback, and a ``client`` property) over the ``EngineClient`` protocol; the nine pure forwards are gone and the bridge reaches ``session.client`` for the reads it publishes. The heartbeat fold and the CRASHED-on-exception fold left ``poll_status`` (the process adapter is now the only liveness owner, and a protocol-violating client whose ``status()`` raises propagates instead of being masked). The session's last-valid-plan copy and its ``rebuild_failed`` re-activation fallback are deleted; the process and in-process adapters each keep their own copy behind the shared protocol, and the child keeps only its revision integer. ``RestartOutcome`` reasons are ``ok``/``rejected``/``restart_failed``. Pinned by ``test_session_surface_is_protocol_plus_its_own_behaviours`` (surface = protocol subset plus own behaviours; ``clear_previews`` stays protocol-owned, one ``session.client`` hop away, with no UI caller), ``test_session_trusts_the_state_the_adapter_folded``, ``test_status_errors_propagate_from_the_client``, ``test_restart_does_not_rebuild_from_session_memory``, and ``test_closed_session_preview_resets_are_no_ops``. Gated by ``uv run check``, the full suite (one pre-existing timing flake in forced-child-termination preview-slot cleanup, green on rerun), and the offscreen ``--smoke-test``.
