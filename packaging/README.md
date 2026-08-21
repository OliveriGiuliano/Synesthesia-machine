# Windows packaging

Release builds use the committed `pysidedeploy.spec` and `uv.lock` to build a Nuitka
standalone-directory application. One-file packaging and an installer are intentionally deferred
until this form has passed the clean-machine gate.

From Windows x64 PowerShell at the repository root:

```powershell
.\packaging\build.ps1
```

The default build requires a clean worktree, synchronizes the locked `packaging` dependency group,
runs all static checks and tests, builds `packaging/out/Synesthesia Machine.dist`, bundles notices and
licence texts, records per-file provenance, and creates a fixed-order/fixed-timestamp ZIP plus
`SHA256SUMS.txt`. `-AllowDirty` and `-SkipTests` exist only for local diagnosis; artifacts produced
with either switch are not release candidates.

Validate the resulting directory from a clean Windows 11 x64 account or VM with:

```powershell
.\packaging\smoke_test.ps1 -AppDirectory '.\packaging\out\Synesthesia Machine.dist'
```

The smoke runner needs PowerShell and Windows only. It deliberately sanitizes `PATH`, copies the app
to a temporary portable-install location, runs the compiled self-test, launches/closes/relaunches the
Qt UI, checks native media/MIDI/audio files and development-file exclusions, deletes the portable
copy, and verifies that a sentinel user document survives.

See `RUNNING.md`, `licensing-review.md`, `release-checklist.md`, and `clean-machine-smoke.md` for the
operational and release gates.
