# ADR-0018: Adopt OpenCV 5.x for camera and image processing

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

ADR-0003 pinned the media stack to OpenCV 4.13.x and excluded OpenCV 5 from the initial
release, when OpenCV 5 was not yet the stable line. As of 2026-09, OpenCV 5.0 (5.0.0.93 on
PyPI for Windows and Linux x64) is the current stable major release, and the 4.x line is in
maintenance.

The application's OpenCV surface is deliberately narrow and stable across 4 and 5: colour
conversion and scaling, resize/flip/affine warping, thresholding, Canny, `filter2D`,
morphology, contour extraction with moments, Farneback optical flow, `checkRange`,
`VideoCapture` with explicit platform backends, and thread-count control. OpenCV 5.0 is built
on the 4.x code base and does not change this surface.

## Decision

Use OpenCV 5.x (locked to 5.0.0.93) for camera capture and CPU-first image processing. This
supersedes the "OpenCV 4.13.x" pin and the "OpenCV 5 excluded from the initial release"
exclusion in ADR-0003. The version constraint in `pyproject.toml` becomes `opencv-python>=5.0,<6`.
The mandatory-GPU-backend exclusion from ADR-0003 remains in force: the project stays
CPU-first on the ordinary OpenCV wheels. PyAV and NumPy selections are unchanged.

## Consequences

The full test suite, strict type-check, and offscreen application smoke pass against
5.0.0.93. OpenCV remains Apache-2.0 licensed; the packaging dependency inventory and
third-party notices regenerate from the locked environment at the next release build.
