"""User-facing media-path normalization shared by runtime and persistence."""

from __future__ import annotations

import os
from pathlib import Path


def normalize_media_path(value: str | Path) -> Path:
    """Return a path after accepting common shell-style surrounding quotes.

    Graph documents are plain JSON text and must stay portable between Windows and
    Linux. Windows-saved documents may carry backslash separators, which are valid
    filename bytes on POSIX; interpret them as separators so those documents open on
    Linux with the same media resolution.
    """

    if isinstance(value, Path):
        return value.expanduser()
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1].strip()
    if os.name != "nt" and "\\" in normalized:
        normalized = normalized.replace("\\", "/")
    return Path(normalized).expanduser()


__all__ = ["normalize_media_path"]
