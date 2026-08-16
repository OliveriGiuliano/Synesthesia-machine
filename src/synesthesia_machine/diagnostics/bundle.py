"""Privacy-aware, bounded diagnostic bundle export."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import cast
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from synesthesia_machine import __version__
from synesthesia_machine.contracts import EngineMetrics, NodeProfile
from synesthesia_machine.diagnostics.hardware import HardwareSnapshot, collect_hardware_snapshot
from synesthesia_machine.graph import GraphSnapshot
from synesthesia_machine.persistence import graph_to_data

DIAGNOSTIC_DISTRIBUTIONS = (
    "av",
    "mido",
    "numpy",
    "opencv-python",
    "psutil",
    "pyside6",
    "python-rtmidi",
    "sounddevice",
)
MAX_LOG_FILES = 5
MAX_LOG_BYTES = 1024 * 1024
REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class DiagnosticBundleResult:
    path: Path
    redacted: bool
    included_files: tuple[str, ...]
    log_files_included: int


def create_diagnostic_bundle(
    destination: str | Path,
    graph: GraphSnapshot,
    *,
    logs_directory: str | Path | None = None,
    engine_metrics: EngineMetrics | None = None,
    node_profiles: Sequence[NodeProfile] = (),
    hardware: HardwareSnapshot | None = None,
    include_paths: bool = False,
) -> DiagnosticBundleResult:
    """Create an atomic ZIP containing text/JSON diagnostics and no frame data."""

    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    included: list[str] = []
    log_count = 0
    hardware_snapshot = hardware or collect_hardware_snapshot()
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            _write_json(archive, "hardware.json", asdict(hardware_snapshot), included)
            _write_json(archive, "dependencies.json", dependency_versions(), included)
            graph_data: object = graph_to_data(graph)
            if not include_paths:
                graph_data = redact_sensitive_paths(graph_data)
            _write_json(archive, "graph.json", graph_data, included)
            _write_json(
                archive,
                "engine_metrics.json",
                {} if engine_metrics is None else asdict(engine_metrics),
                included,
            )
            _write_json(
                archive,
                "node_profiles.json",
                [asdict(profile) for profile in node_profiles],
                included,
            )
            for name, content in _bounded_logs(logs_directory, include_paths=include_paths):
                archive.writestr(name, content)
                included.append(name)
                log_count += 1
            manifest = {
                "application_version": __version__,
                "redacted": not include_paths,
                "included_files": [*included, "manifest.json"],
                "log_files_included": log_count,
                "frames_included": False,
            }
            _write_json(archive, "manifest.json", manifest, included)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return DiagnosticBundleResult(output, not include_paths, tuple(included), log_count)


def dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in DIAGNOSTIC_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not installed"
    return versions


def redact_sensitive_paths(value: object) -> object:
    """Return a JSON-shaped copy with path fields and absolute path strings removed."""

    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        items = cast("Mapping[object, object]", value)
        for raw_key, item in items.items():
            key = str(raw_key)
            if isinstance(item, str) and _key_contains_path(key):
                result[key] = REDACTED
            else:
                result[key] = redact_sensitive_paths(item)
        return result
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
        return [redact_sensitive_paths(item) for item in items]
    if isinstance(value, str) and _is_absolute_path(value):
        return REDACTED
    return value


def _bounded_logs(
    logs_directory: str | Path | None,
    *,
    include_paths: bool,
) -> tuple[tuple[str, str], ...]:
    if logs_directory is None:
        return ()
    directory = Path(logs_directory)
    if not directory.is_dir():
        return ()
    candidates = sorted(
        (path for path in directory.iterdir() if path.is_file() and _is_log_file(path)),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:MAX_LOG_FILES]
    logs: list[tuple[str, str]] = []
    for index, path in enumerate(candidates, start=1):
        raw = path.read_bytes()[-MAX_LOG_BYTES:]
        text = raw.decode("utf-8", errors="replace")
        if not include_paths:
            text = _redact_log_lines(text)
        logs.append((f"logs/{index:02d}-{path.name}", text))
    return tuple(logs)


def _is_log_file(path: Path) -> bool:
    name = path.name.casefold()
    return bool(re.search(r"\.(?:log|txt|jsonl)(?:\.\d+)?$", name))


def _redact_log_lines(text: str) -> str:
    # Redact from an absolute-path marker to end-of-line. Over-redacting trailing
    # diagnostic text is preferable to leaking an unquoted path containing spaces.
    return re.sub(r"(?i)(?:[A-Z]:\\|/)[^\r\n]*", REDACTED, text)


def _write_json(archive: ZipFile, name: str, value: object, included: list[str]) -> None:
    archive.writestr(
        name,
        json.dumps(_json_compatible(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    included.append(name)


def _json_compatible(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, Mapping):
        items = cast("Mapping[object, object]", value)
        return {str(key): _json_compatible(item) for key, item in items.items()}
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
        return [_json_compatible(item) for item in items]
    return repr(value)


def _key_contains_path(key: str) -> bool:
    lowered = key.casefold()
    return "path" in lowered or lowered.endswith("directory")


def _is_absolute_path(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return PureWindowsPath(candidate).is_absolute() or PurePosixPath(candidate).is_absolute()


__all__ = [
    "DiagnosticBundleResult",
    "create_diagnostic_bundle",
    "dependency_versions",
    "redact_sensitive_paths",
]
