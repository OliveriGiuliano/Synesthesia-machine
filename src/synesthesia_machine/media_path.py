"""User-facing media-path normalization shared by runtime and persistence."""

from __future__ import annotations

from pathlib import Path


def normalize_media_path(value: str | Path) -> Path:
    """Return a path after accepting common shell-style surrounding quotes."""

    if isinstance(value, Path):
        return value.expanduser()
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1].strip()
    return Path(normalized).expanduser()


__all__ = ["normalize_media_path"]
