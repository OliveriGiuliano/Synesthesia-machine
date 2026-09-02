# ADR-0012: Support Windows and Linux x64 as first-class platforms

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

The product, its ADRs, and its packaging all targeted Windows 11 x64 exclusively. The
engineering stack selected by ADR-0002 through ADR-0006 (PySide6/Qt, NumPy/OpenCV, PyAV,
Mido/python-rtmidi, sounddevice/PortAudio, uv) publishes Linux x64 wheels and behaves the
same way on Linux; only a narrow set of platform-specific choices locked the product to
Windows:

- Per-user state lived under `%LOCALAPPDATA%` only.
- Camera capture tried only the Media Foundation and DirectShow OpenCV backends.
- The window icon was the `.ico` resource only.
- Graph documents persisted media paths with `str(Path)`, producing backslash separators on
  Windows that do not resolve on POSIX hosts.
- Release tooling (`tools.release check`, `packaging/build.ps1`, `pysidedeploy.spec`, the
  packaged smoke runner) validated and built Windows-only artefacts.

Development is moving to Linux while Windows remains a release target, so both platforms must
stay first-class: the same source tree, the same `uv.lock`, and the same test suite must build
and pass on both.

## Decision

- Supported release platforms are Windows 11 x64 and Linux x64 (glibc). macOS remains out of
  scope. This supersedes the "Windows 11, 64-bit" target platform and the "macOS or Linux
  support" exclusion recorded in the master architecture document.
- Per-user application state uses `%LOCALAPPDATA%\SynesthesiaMachine` on Windows and
  `$XDG_DATA_HOME/SynesthesiaMachine` (default `$HOME/.local/share/SynesthesiaMachine`) on
  Linux.
- Camera AUTO backend order is platform-dependent: Media Foundation then DirectShow on Windows,
  V4L2 on Linux. Explicit backend preferences remain selectable on every platform so documents
  stay portable; a backend absent from the host is a normal unavailable state, not a failure.
- The window icon is the multi-resolution `.ico` on Windows and the bundled PNG master on
  Linux.
- Graph JSON persists relative media paths in POSIX form on every platform; POSIX hosts
  interpret backslash separators in media paths (as written by Windows builds) as path
  separators so documents saved on one platform open on the other.
- `pysidedeploy.spec` remains the Windows deployment specification; `pysidedeploy.linux.spec`
  is the Linux deployment specification. `packaging/build.sh` and `packaging/smoke_test.sh`
  mirror `build.ps1`/`smoke_test.ps1` for Linux hosts. `tools.release check` validates the
  specification, versions, and machine of the host platform.
- On Linux the sounddevice runtime resolves PortAudio from the system (`libportaudio2`); the
  Windows wheels continue to bundle their PortAudio binary. A machine without PortAudio cannot
  use the optional debug-audio feature, which the packaged smoke reports as skipped, not
  failed.

## Consequences

- CI runs the quality job on `windows-latest` and `ubuntu-latest`.
- Nuitka cannot cross-compile, so each platform's standalone deliverable is built on that
  platform from the same committed source, `uv.lock`, and version.
- The clean-machine Linux smoke additionally assumes a desktop base system with the Qt shared
  libraries (GL/EGL, fontconfig, xkbcommon) and `libportaudio2`; a machine missing only
  PortAudio passes with the audio check skipped.
- ADR-0001 through ADR-0006 remain accepted; their Windows-specific wording is historical
  context superseded by this ADR where the platform target is concerned.
