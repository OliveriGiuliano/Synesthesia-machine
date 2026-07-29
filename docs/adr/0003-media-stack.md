# ADR-0003: Use PyAV, NumPy, and OpenCV for media

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

File playback needs reliable timestamps, while camera input and image operations need mature
Windows-native implementations.

## Decision

Use PyAV 18.x for FFmpeg-backed file decode and presentation timestamps, NumPy float32 arrays for
runtime values, and OpenCV 4.13.x for camera and CPU-first image processing. OpenCV 5 and mandatory
GPU backends are excluded from Phase 0.

## Consequences

Codec and distribution licences require review before release. Images stay inside the engine
process and do not cross ordinary multiprocessing queues.