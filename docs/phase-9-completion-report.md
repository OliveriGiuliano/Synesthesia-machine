# Phase 9 completion report — Packaging and release hardening

## Outcome

Phase 9 engineering is complete. Commit `53488f7` produced a Windows 11 x64 Nuitka standalone
directory from the committed lockfile, without a debug console or Python source files. The compiled
artifact passed the full packaged runtime matrix on the build host with its child `PATH` restricted to
Windows system directories; Python, `uv`, the repository, and the development virtual environment were
therefore unavailable to the launched copy.

This is an internal evaluation artifact, not an externally distributable release. Two formal release
gates remain held: the same ZIP still needs execution on a genuinely clean Windows 11 x64 VM/new user
account, and the owner has not selected the application licence/distribution model or completed the
required Qt/FFmpeg/native dependency legal review. Phase 10 was not started.

## Build and artifacts

The clean build command was:

```powershell
.\packaging\build.ps1
```

It synchronized `uv.lock`, validated version/deploy invariants, ran formatting/lint/type checks, ran
all 768 tests, compiled and linked 318 C translation units with MSVC, bundled notices/licence files,
hashed the standalone tree, and created a fixed-order/fixed-timestamp ZIP.

| Item | Result |
| --- | --- |
| Source commit | `53488f78da58c75e34b58542d20bf36d30d93324` |
| Worktree in provenance | `dirty: false` |
| Standalone directory | `packaging/out/Synesthesia Machine.dist` (local, ignored) |
| Executable | `SynesthesiaMachine.exe`, 26,400,256 bytes |
| ZIP | `Synesthesia-Machine-0.1.0-windows-x64.zip`, 113,014,923 bytes |
| ZIP SHA-256 | `a75916b455f469ab9bc36cbcdfef32eb0b45cd5d9b0772fa4ecc4cef8186abd9` |
| Archive reproducibility | second archive with recorded source epoch produced the same SHA-256 |
| Windows resources | product `Synesthesia Machine`; file/product version `0.1.0.0`; seven icon sizes |
| Toolchain | Python 3.12.13, PySide6 6.10.3, Nuitka 4.1, MSVC 14.3, uv 0.12.0 |

The committed evidence is [`phase-9-build-provenance.json`](phase-9-build-provenance.json). It records
the source/lock/spec hashes, toolchain, and 202 pre-provenance artifact file hashes. The finished
directory contains 203 files, including 38 copied third-party licence/notice files. It contains no
`.py`, `.pyc`, or `.pyo` files and no ASIO-enabled DLL.

## Packaged smoke evidence

The test command was:

```powershell
.\packaging\smoke_test.ps1 `
  -AppDirectory '.\packaging\out\Synesthesia Machine.dist' `
  -ReportPath '.\packaging\out\packaged-smoke-report.json' `
  -Headless
```

The runner copied the artifact to a temporary portable-install directory, restricted the child
`PATH`, executed the compiled self-test, launched/closed/relaunched Qt twice, deleted the portable
copy, and verified that a sentinel `.synmachine.json` file in Documents survived. The committed
machine-readable result is [`phase-9-packaged-smoke.json`](phase-9-packaged-smoke.json).

| Required row | Build-host packaged result |
| --- | --- |
| Launch/close/relaunch | Passed twice through the compiled GUI executable |
| Create/save/open graph | Passed one-node deterministic round trip |
| H.264/MP4 | Decoded a 96×64 H.264 frame from the bundled fixture |
| Camera | Enumerated one camera and captured one frame from `opencv:0` |
| MIDI | Enumerated two real outputs; mock note send and panic passed |
| Debug audio | PortAudio loaded 27 records; synth render and brief hardware stream passed |
| Engine crash/restart | Forced child exit and replaced PID 20812 with PID 11412 |
| Autosave recovery | Recovery record was written, discovered, and loaded |
| Diagnostics | Redacted ZIP export passed and included no frame data |
| Portable deletion | User-document sentinel survived application-directory removal |

This host has a development environment installed, so the sanitized `PATH` run is strong packaged-path
evidence but does not replace the packet's clean-VM/new-account acceptance step.

## Packaging and runtime decisions

- `pysidedeploy.spec` is standalone-directory only; one-file mode is absent.
- Nuitka 4.1 is pinned in both `pyproject.toml` and `uv.lock`. PySide6 6.10.3's older deploy template
  suggested Nuitka 2.7.11, but that compiler crashed on NumPy 2.5's generic type aliases. The current
  line also required explicit inclusion of the dynamic `av.utils` extension discovered by the
  compiled smoke test.
- The GUI subsystem disables the console and embeds the same multi-resolution icon used by Qt.
- Mido backends, RtMidi, `_sounddevice_data`, and the dynamic PyAV utility extension are explicit
  includes. The default PortAudio DLL is copied and verified; `*asio*.dll` is excluded both during
  compilation and by a post-build release assertion.
- Qt modules are explicitly `Core,Gui,Widgets`. `pyside6-deploy` could not find `dumpbin` from its
  process environment, so its optional dependency scan warned; the compiled Qt runtime and plugins
  nevertheless passed both UI launch cycles.
- No installer was created. The packet requires standalone proof first, and the clean-VM/legal gates
  are not yet cleared.

## Paths and release controls

[`packaging/RUNNING.md`](../packaging/RUNNING.md) documents logs, recovery, settings, diagnostics, and
user-document locations. [`packaging/release-checklist.md`](../packaging/release-checklist.md) defines
version/tag immutability, signing, artifact retention, and rollback without deleting user data.
Signing is planned but not performed because no managed certificate/timestamping service was supplied.

The deterministic build and evidence tools are in [`packaging/build.ps1`](../packaging/build.ps1) and
[`tools/phase9_release.py`](../tools/phase9_release.py). The clean-machine procedure is
[`packaging/clean-machine-smoke.md`](../packaging/clean-machine-smoke.md).

## Licensing and distribution gate

The generated inventory covers 14 transitive runtime distributions, 78 native environment files,
FFmpeg library versions, and 557 available codec names. The PyAV wheel contains FFmpeg plus x264,
x265, LAME, OpenCORE AMR, dav1d, SVT-AV1, VPL, VPX, WebP, and other native components while not
exposing its complete FFmpeg configure flags.

External distribution remains blocked by [`LICENSE-or-NOTICE.md`](../LICENSE-or-NOTICE.md) and
[`packaging/licensing-review.md`](../packaging/licensing-review.md). The owner must choose an
application licence/distribution model and obtain competent review of Qt/PySide/Shiboken,
PyAV/FFmpeg/codecs, OpenCV, Mido/RtMidi, sounddevice/PortAudio/CFFI, NumPy, and all transitives. The
standalone directory bundles [`THIRD_PARTY_NOTICES.md`](../packaging/THIRD_PARTY_NOTICES.md), the
machine inventory, and available raw licence texts, but that inventory is not legal approval.

## Remaining release gates

1. Run the exact ZIP/hash through `packaging/clean-machine-smoke.md` on a pristine supported Windows
   11 x64 VM or new standard user account and archive the automated report plus manual observations.
2. Select and record the application licence and distribution model; complete competent legal review.
3. If public delivery is intended, provision signing, sign after reproducible unsigned validation,
   verify the delivered signatures, and then evaluate an installer as a separate gate.

No Phase 10 work was performed.
