# Media, hardware, diagnostics, performance, and release playbook

## Changing media, MIDI, audio, or diagnostics

- File video uses PyAV timestamps; camera capture and image operations use the approved OpenCV/NumPy
  stack. Preserve source lifecycle, bounded queues, deterministic generated fixtures, and exact
  metadata semantics.
- Physical device absence is a normal unavailable state. Never select a fallback camera, MIDI port,
  or output device when the user requested an exact identity.
- Automated tests must not open a physical camera, send MIDI, or play audio. Inject capture factories,
  use `MockMidiBackend`, and call audio callbacks with preallocated arrays.
- Hardware commands are opt-in. `tools.camera_probe` opens cameras, `tools.audio_probe --play` emits
  sound, and `tools.midi_hardware_evidence` sends real MIDI only after two exact matching port arguments.
- Diagnostic bundles are bounded, redact filesystem paths by default, exclude frame/image pixels, and
  include paths only with explicit user consent. Never add credentials, environment secrets, or raw
  user media to logs or evidence.
- Performance changes need correctness tests first and measurements second. Do not make performance
  claims from one noisy run, change a gate to bless a regression, or round a failing result into a
  pass. ADR-0007 documents the accepted reference-throughput interpretation.

## Changing packaging or versions

- The source version in `src/synesthesia_machine/version.py`, project version in `pyproject.toml`, and
  product/file versions in `pysidedeploy.spec` must agree. Use
  `uv run python -m tools.release check` to verify them.
- Read all of `packaging/README.md`, `packaging/release-checklist.md`, and
  `packaging/licensing-review.md` before release work.
- `packaging/build.ps1` is not an ordinary test command. It synchronizes packaging dependencies,
  runs gates, deletes and recreates repository-owned `packaging/out`, `packaging/work`, and
  `deployment`, and refuses a dirty tree unless `-AllowDirty` is passed. An `-AllowDirty` or
  `-SkipTests` artifact is never a release candidate.
- The default release is a standalone directory, not one-file packaging or an installer. Preserve the
  native runtime checks, ASIO exclusion, notices/licenses, deterministic archive ordering/timestamps,
  provenance, and clean-machine smoke gate.
- The repository does not yet declare an approved application license or distribution model. Do not
  publish or represent an artifact as legally cleared. Dependency/codec and third-party notice review
  remain release blockers until explicitly approved.
- Never delete user graph documents or the application data root (`%LOCALAPPDATA%\SynesthesiaMachine`
  on Windows, `~/.local/share/SynesthesiaMachine` on Linux) during packaging, rollback, uninstall, or
  smoke testing.
