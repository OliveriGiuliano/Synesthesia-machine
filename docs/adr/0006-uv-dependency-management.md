# ADR-0006: Use uv for dependency management

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Binary multimedia and Qt dependencies need reproducible resolution on Windows. Developers also
need one documented command surface.

## Decision

Use `uv`, `pyproject.toml`, a project-local `.venv`, and a committed `uv.lock`. Pin Python to 3.12
and constrain major/minor dependency families selected by the architecture. Hatchling is the
minimal PEP 517 build backend; pytest, Ruff, and Pyright are development-only dependencies.

## Consequences

`uv sync --locked` is the reproducibility gate. Every dependency addition requires written
justification and a lockfile update. Global packages and the user's unrelated Conda environments
are not project inputs.