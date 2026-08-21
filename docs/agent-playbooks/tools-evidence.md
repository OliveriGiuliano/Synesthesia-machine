# Tools and generated evidence playbook

Read `tools/README.md` before executing a diagnostic. Many tools intentionally overwrite committed
evidence when run without `--output`, including phase performance, soak, profiler, and benchmark JSON
or screenshots under `docs/`. Use a path under the system temporary directory for exploratory runs
when the tool supports it, and inspect `git status` immediately afterward.

Important distinctions:

- `tools.validate_graphs` is read-only unless `--output` is supplied.
- `tools.generate_catalogue` intentionally overwrites the current catalogue.
- `tools.generate_test_video` writes the path supplied by the caller.
- UI diagnostics launch Qt and write JSON plus screenshots by default.
- Benchmark and soak tools write tracked `docs/evidence/` outputs by default and may be long-running.
- Hardware probes may enumerate or open real devices; only run the explicitly invasive modes with
  user authorization and an exact target.
- Release scripts generate or replace packaging artifacts and must be treated as release operations.

Never edit measured evidence by hand to manufacture a pass. If an intentional canonical run updates
evidence, keep the environment, dependency versions, Git state, parameters, and pass/fail result
truthful and update the corresponding report when required.
