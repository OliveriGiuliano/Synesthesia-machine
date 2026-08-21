# Phase 8 — Performance, diagnostics, and optimization

## Objective

Measure the whole application, expose bottlenecks to users, and meet the defined reference-graph target through evidence-driven optimization.

## Reference target

500×500, 60 fps source:

Synthetic/Camera → Resize → Gaussian Blur 5×5 → RGB-to-HSV → Separate Channels → Channel-to-Pitch → Note Visualizer, plus HSV → Canny → Display Image.

After five-second warm-up: median processed FPS at least 60 where source supplies it; p95 graph-execution latency below 16.7 ms on designated representative hardware; bounded memory/queues and responsive UI.

## Metrics

Input/processed/preview FPS; frame age; Nth-frame skips; backpressure drops; CPU/RAM; queue occupancy; per-node last/EMA/p50/p95/max; errors; output shape/bytes; optional NVIDIA VRAM through an adapter that can be unavailable.

## Optimization order

1. accidental copies and colour conversions;
2. demand-root pruning and hidden preview work;
3. static and shape-dependent caches;
4. preview and metrics throttling;
5. source queue/pacing;
6. OpenCV thread-count experiments;
7. branch parallelism behind a feature flag;
8. optional GPU proof only for measured bottlenecks.

Do not begin by adding threads or CUDA.

## Allowed paths

`runtime/profiling.py`, diagnostics package/UI, node timing hooks, benchmark tools/tests, and targeted implementation modules supported by profiles.

## Required deliverables

Profiler table; graph heatmap; reset/freeze/copy report; psutil metrics; diagnostic bundle with optional path redaction; benchmark harness with hardware/version capture; soak test; written baseline and optimized results.

## Required tests

Metric aggregation accuracy; throttling; profiler-off overhead; diagnostic redaction; bounded rolling windows; benchmark reproducibility checks; 60-minute soak; stuck-note check during overload.

## Exit criteria

Target met or an ADR records measured limitation and revised target. Normal profiler overhead under 3%. No meaningful memory growth during soak.

## Completion report

Baseline; profiles; changes; before/after measurements; tests; unresolved bottlenecks; deviations; next work.
