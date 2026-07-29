$ErrorActionPreference = "Stop"

Write-Host "Running Synesthesia Machine source smoke checks"
uv sync --locked
uv run synmachine --smoke-test
uv run check
uv run test