# 02: Consolidate the engine-side preview transport into a PreviewChannel

**What to build:** A preview flowing from a producing node port to the UI has one transport module with two adapters, instead of three separate implementations that can drift. A maintainer changes preview-slot lifetime or the seqlock protocol in one place and it applies identically whether the engine runs in-process or in the spawned child process; a test exercises the channel with a fake transport instead of spawning a child.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] One PreviewChannel module exposes a small publish/poll interface with two adapters: in-process direct, and shared-memory seqlock.
- [x] Both the child-process and in-process preview paths publish through the same channel; the three prior implementations are removed.
- [x] Preserves ADR-0008/0009: producer-port anchoring, the bounded latest-result worker, cadence + 800px caps, and the process boundary (full-resolution frames stay engine-local) are unchanged.
- [x] The child preview state machine is testable with a fake transport (no private-import workaround, no mandatory child spawn).
