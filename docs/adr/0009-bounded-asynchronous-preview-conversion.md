# ADR-0009: Convert live previews on a bounded latest-result worker

## Status

Accepted.

## Context

ADR-0008 made every connected image/channel producer port eligible for a 30 Hz live preview. The
graph worker then converted every due full-resolution value before completing its source tick. On the
Phase 8 reference graph, six preview targets changed an otherwise 14 ms graph tick into a bimodal
14/33 ms workload and reduced measured processing from approximately 60 FPS to 42.67 FPS. The preview
work also allocated avoidable intermediate arrays for ordinary finite frames.

Preview conversion is presentation work. It must remain bounded and generation-safe, but it must not
delay graph execution or cause source frames to accumulate.

## Decision

- Keep the preview ownership, per-port routing, 800-pixel dimension cap, shared memory protocol, and
  UI behavior from ADR-0008. Keep dock-selected image/channel outputs at 30 Hz, while pill-only
  outputs use a 15 Hz cap to avoid multiplying background conversion bandwidth across large graphs.
  This supersedes ADR-0008's single application-wide 30 Hz image/channel cadence.
- Submit completed tick results to one engine-owned preview worker. The worker retains at most one
  pending result while converting one active result; a newer completion replaces stale pending work.
- Stamp every job with the broker generation at submission. A graph activation invalidates older jobs,
  including a job that finishes conversion after the new plan commits.
- Treat preview-worker failure as engine runtime failure, matching the former synchronous behavior.
- Wait for both graph and preview work in explicit idle waits and stop the preview worker during normal
  or failed engine cleanup.
- Reuse one immutable conversion for aliased frame objects within a tick. Use allocation-free native
  conversion for ordinary finite/in-range preview values and sanitizing copies for exceptional values.

## Consequences

- Preview conversion cannot extend graph execution latency or directly cause source-mailbox
  replacement.
- Preview output may intentionally skip intermediate completed ticks when conversion is slower than
  production. This is the same latest-data preference used by the source mailbox.
- Connection pills remain visually responsive at up to 15 updates per second, while the larger image
  dock retains its 30 Hz cap.
- At most two distinct completed tick results are retained by preview publication (one active and one
  pending/latest result), so memory remains bounded. Full-resolution arrays still never cross the
  process boundary.
- Preview CPU and memory-bandwidth work can still compete with graph algorithms, but stale work is not
  queued and common conversions allocate substantially less temporary storage.

## References

- `docs/adr/0008-per-connection-live-previews.md`
- `src/synesthesia_machine/runtime/in_process_engine.py`
- `src/synesthesia_machine/runtime/previews.py`
- `docs/phase-8-completion-report.md`
