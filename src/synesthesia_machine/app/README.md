# App package

Composition root, CLI entry point, paths, logging setup, application registry, and smoke-test mode.

`bootstrap.py` is the only normal assembly path and must retain Windows `freeze_support()` before
process creation. Package modules must not perform work at import time. Keep platform/hardware
composition here while domain behavior remains in the lower headless packages and Qt behavior in
`ui`.

Use `uv run synmachine --smoke-test` for a real offscreen shell lifecycle check; CI runs it after the
static and pytest gates.
