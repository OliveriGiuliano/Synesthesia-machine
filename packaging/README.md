# Standalone packaging (Windows and Linux)

Release builds use the committed deployment specifications and `uv.lock` to build a Nuitka
standalone-directory application, per platform:

- Windows x64: `pysidedeploy.spec`
- Linux x64: `pysidedeploy.linux.spec`

One-file packaging and an installer are intentionally deferred until this form has passed the
clean-machine gate on both platforms. Nuitka cannot cross-compile, so each platform's deliverable
is built on that platform from the same committed source, `uv.lock`, and version (ADR-0012).

From a Windows x64 PowerShell or a Linux x64 bash shell at the repository root:

```powershell
.\packaging\build.ps1
```

```bash
./packaging/build.sh
```

The default build requires a clean worktree, synchronizes the locked `packaging` dependency group,
runs all static checks and tests, builds `packaging/out/Synesthesia Machine.dist`, bundles notices and
licence texts, records per-file provenance, and creates a fixed-order/fixed-timestamp ZIP plus
`SHA256SUMS.txt`. `--allow-dirty`/`-AllowDirty` and `--skip-tests`/`-SkipTests` exist only for local
diagnosis; artifacts produced with either flag are not release candidates.

Validate the resulting directory from a clean Windows 11 x64 account/VM or a clean Linux x64
machine:

```powershell
.\packaging\smoke_test.ps1 -AppDirectory '.\packaging\out\Synesthesia Machine.dist'
```

```bash
./packaging/smoke_test.sh '/path/to/Synesthesia Machine.dist'
```

The smoke runners sanitize `PATH`, copy the app to a temporary portable-install location, run the
compiled self-test, launch/close/relaunch the Qt UI, check native media/MIDI/audio files and
development-file exclusions, delete the portable copy, and verify that a sentinel user document
survives. The Windows runner needs PowerShell; the Linux runner needs bash and the desktop base
libraries (Qt GL/EGL/fontconfig/xkbcommon, `libportaudio2` for the audio check, which is reported
skipped rather than failed when absent). The Linux runner uses the offscreen Qt platform by default
so it also passes on display-less machines; pass `--display` to run the Qt UI on the real display,
mirroring the Windows runner's default.

See `RUNNING.md`, `licensing-review.md`, `release-checklist.md`, and `clean-machine-smoke.md` for the
operational and release gates.
