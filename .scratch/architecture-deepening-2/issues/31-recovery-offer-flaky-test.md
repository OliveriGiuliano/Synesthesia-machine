# 31: Stabilise the intermittently failing recovery-offer discard test

**What to build:** `tests/ui/test_document_lifecycle.py::test_offer_recovery_discard_continues_through_all_records` fails intermittently on `main`. It writes two recovery records (node x=10.0 then x=30.0), sets the recovery choice to Discard, calls `controller.offer_recovery()`, and asserts `autosave_store.discover() == ()`. On a bad run the assertion gets a one-record tuple instead of the empty one: `assert (RecoveryRecord(document_id=..., path=..., modified_time_ns=..., explicit_path=None),) == ()` — one candidate survived `offer_recovery()` instead of every record being discarded. Observed 2026-09-17: it failed on four consecutive full-suite runs and again in an isolated run, but passed in an earlier run — genuinely flaky, not a regression from the ticket 28/29/30 changes (it fails identically with those stashed).

**Solution:** Diagnose the root cause and fix it deterministically. Two candidate seams to inspect first:
- `AutosaveStore.discover` (`src/synesthesia_machine/persistence/autosave.py:72`) sorts candidates by `st_mtime_ns` descending with no tie-break. The two test saves land back-to-back and can share a filesystem mtime tick, so the discovery order of the two records then falls back to arbitrary `glob` iteration order.
- `DocumentLifecycleController.offer_recovery` (`src/synesthesia_machine/ui/document_lifecycle.py:225`) iterates a single snapshot of `discover()` and, per record, skips any candidate where `RecoveryRecord.is_newer_than(explicit_path)` is false (`autosave.py:34`). A skipped record is never discarded and therefore survives to the final `discover()` assertion.

Prefer making discovery order deterministic (add a stable secondary sort key, e.g. document id / path, so equal mtimes never depend on directory-iteration order) and/or pinning distinct, controlled mtimes in the test helper so the offer/discard path is exercised deterministically. Confirm the fix addresses the mechanism rather than the symptom, then pin it so the test is stable.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/persistence/autosave.py`
- `src/synesthesia_machine/ui/document_lifecycle.py`
- `tests/ui/test_document_lifecycle.py`

## Answer

**Root cause** (corrects the mtime-tie hypothesis above): the flake is a test-versus-async race, not a filesystem-mtime or ordering issue. `offer_recovery` routes each discard through `AutosaveController.discard`, which submits `store.discard` to a background `ThreadPoolExecutor(max_workers=1)`. The test asserts `store.discover() == ()` on the UI thread immediately after `offer_recovery` returns, so it races the single worker thread. The worker runs the two discards FIFO (newest first); by the time the assertion runs the newer discard has usually landed but the older one is still in flight, so the older record survives. Reproduced on this host (nanosecond-mtime ext4) across 200 in-process runs, confirming it is not mtime-granularity dependent.

**Fix:** `AutosaveController.__init__` now accepts an optional `executor` (default: the real `ThreadPoolExecutor`, so production behaviour is unchanged). The offscreen lifecycle test injects a synchronous `_InlineExecutor` that runs `submit` on the calling thread, so `offer_recovery`'s discards settle before the call that queued them returns and the assertion observes settled state.

**Files:**
- `src/synesthesia_machine/ui/autosave_controller.py` (injectable `executor`, default unchanged)
- `tests/ui/test_document_lifecycle.py` (`_InlineExecutor` helper, injected into the test env)

Commit: the change resolving this ticket (autosave_controller.py + test_document_lifecycle.py in one commit).

**Acceptance:**
- [x] Root cause identified and documented: background-executor discard racing the synchronous test assertion (not the mtime tie hypothesised at filing)
- [x] `AutosaveController` executor is injectable; the default stays the real single-thread executor, so production is unchanged
- [x] The lifecycle test env injects a synchronous `_InlineExecutor`, removing the race rather than masking it
- [x] `test_document_lifecycle.py` passes 38/38 repeated runs (previously roughly 50% flaky)
- [x] `uv run check` green; full suite 1326 passed / 3 skipped / 0 failed; no unrelated diff
