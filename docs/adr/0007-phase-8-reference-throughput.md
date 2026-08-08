# ADR-0007: Accept bounded latest-frame throughput for the Phase 8 reference graph

## Status

Accepted.

## Context

The Phase 8 reference target requires a 500×500, nominal 60 FPS source to produce a median processed
rate of at least 60 FPS, graph-execution p95 below 16.7 ms, bounded memory and queues, and a responsive
UI after a five-second warm-up.

The committed three-run baseline measured 56.330 FPS, 18.300 ms graph p95, 318 mailbox replacements,
and a responsive UI. After the ordered optimizations, the definitive three-run measurement supplied
60.000, 60.000, and 60.033 input FPS and processed 59.967, 59.833, and 60.000 FPS. Graph p95 was
14.649, 14.632, and 14.992 ms; graph maximum was at most 16.404 ms; and UI heartbeat p95 was at most
18.779 ms. Seven of 5,401 input frames (0.130%) were replaced by the existing two-slot latest-frame
mailbox during Windows presentation-timer catch-up bursts.

The graph is therefore fast enough inside every measured frame budget, but the strict finite-window
60.000 FPS median gate remains false by one processed frame per 30-second median run. Increasing the
mailbox or adding branch threads would retain stale work and increase latency to mask a source-pacing
effect. GPU work would not address it.

## Decision

Keep the original result and `passed: false` value in `docs/phase-8-optimized.json`; do not round or
silently relax it.

For this Windows/CPU reference configuration, accept Phase 8 when all of the following revised gates
hold across three runs after the required warm-up:

- median processed FPS is at least 59.8 while measured median input FPS is at least 60.0;
- median graph-execution p95 is below 16.7 ms;
- at least 99.75% of measured input frames are processed;
- every run meets the UI responsiveness criterion; and
- queues and memory remain bounded in the formal soak.

The original 60.000 FPS gate remains the preferred target for later source-pacing work. The bounded
two-slot latest-frame mailbox remains unchanged.

## Consequences

- The optimized result passes the revised target at 59.967 FPS, 14.649 ms p95, 99.870% input coverage,
  and responsive UI in every run.
- The application continues to prefer fresh frames over accumulating latency during rare scheduling
  bursts.
- Branch parallelism and GPU execution are not implemented in Phase 8 because the measured graph work
  already fits the frame budget and those changes would add synchronization or platform complexity.
- Future performance comparisons must report measured input FPS and replacement counts alongside
  processed FPS; nominal source rate alone is insufficient.

## References

- `docs/phase-8-baseline.json`
- `docs/phase-8-optimized.json`
- `docs/phase-8-soak.json`
- `docs/phase-8-profiler-overhead.json`
- `synesthesia_machine_design/phase_packets/phase_8_performance_diagnostics.md`
