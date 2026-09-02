# Release checklist, versioning, signing, and rollback

## Candidate gate

- [ ] Application licence and distribution model approved in writing.
- [ ] Third-party/codec review in `licensing-review.md` approved by competent counsel.
- [ ] Worktree clean; annotated candidate commit reviewed.
- [ ] `pyproject.toml`, `version.py`, and the platform `pysidedeploy.spec` versions agree.
- [ ] `uv.lock` is current and the build runs on the deliverable platform (Windows 11 x64 or Linux x64).
- [ ] `packaging/build.ps1` (Windows) or `packaging/build.sh` (Linux) completes without dirty/skip flags.
- [ ] Archive hash matches `SHA256SUMS.txt`; provenance records `dirty: false`.
- [ ] Clean-machine automated and manual smoke matrix passes with evidence on the deliverable platform.
- [ ] No console window, source/tests/caches, credentials, user media, or unreviewed DLLs ship.
- [ ] Previous accepted standalone archive and its hash remain available for rollback.

## Version and tag

Use semantic versions. Update the project, source, and platform version metadata (four-component
Windows file version) together; run the full build on each deliverable platform; then create an
annotated tag `vMAJOR.MINOR.PATCH` at the exact clean source commit.
Never move or reuse a published tag. Store the ZIP, SHA-256 file, provenance, dependency inventory,
notices, smoke evidence, and approval record together.

## Code-signing plan

Signing is deferred until a managed certificate and timestamping service are available. When enabled,
keep private keys outside the repository and build host, sign `SynesthesiaMachine.exe` and the final
installer (if one is later approved), use SHA-256 digest and an RFC 3161 timestamp, then verify the
signature on an offline clean VM. Generate provenance before signing for unsigned content hashes and
again after signing for delivered hashes. CI logs must record certificate subject/thumbprint and
timestamp result, never secrets. A missing, expired, invalid, or unexpected signature blocks release.

## Rollback

Stop distribution of the rejected version; publish the last accepted ZIP and hash without rewriting
its tag; preserve the failed artifact and evidence for investigation. Portable rollback replaces only
the application directory. Do not delete `%LOCALAPPDATA%\SynesthesiaMachine` or user graph documents.
If a document-schema rollback is incompatible, retain the newer app until an explicit backward
migration exists—never downgrade documents destructively.

An installer remains out of scope until the standalone clean-machine gate passes. Any future installer
must preserve user documents and application data by default and must be tested separately.
