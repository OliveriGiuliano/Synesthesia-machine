# 10: Deterministic lifecycle seam for source services

**What to build:** The pause/resume/seek race invariants are only expressible by poking private fields: test_video_source.py:83-100 assigns source._wake_event, acquires source._lock, sets source._state, and calls source._wait_until_playing(). The public interface is shallow exactly where the state machine is risky, and a refactor that moves a lock would break tests that poke fields that no longer exist.

**Solution:** Put the wake/stop primitives on the injectable surface — extend the clock/protocol protocols (or accept pre-created events) so a test harness can inject controllable wake and stop objects — and drive the race scenarios through the public play/pause/resume/seek/close API.

**Status:** resolved

**Files:**
- `src/synesthesia_machine/media/video_source.py`
- `src/synesthesia_machine/media/camera_source.py`
- `tests/media/test_video_source.py`
- `tests/media/test_camera_source.py`

**Acceptance:**
- [x] Races testable through the real interface
- [x] Race knowledge stays in the service
- [x] Lock moves break tests honestly
- [x] No more object-private surgery in tests
- [x] Targeted tests pass; `uv run check` green; no unrelated diff
