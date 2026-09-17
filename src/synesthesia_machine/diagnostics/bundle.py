"""Privacy-aware, bounded diagnostic bundle export."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum, StrEnum
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

# Path shapes that must never leak into a redacted bundle. They are matched
# as substrings so paths embedded in tracebacks and messages are covered,
# not only string values that are entirely a path.
_PATH_TOKEN_PATTERN = re.compile(
    r"(?:"
    r"[A-Za-z]:\\[^\s\"',:;|?*)\]]+"
    r"|"
    r"\\\\[^\s\"',;|?*)\]]+"
    r"|"
    r"/[^\s\"',;|?*)\]]+"
    r"|"
    r"~/[^\s\"',;|?*)\]]+"
    r")"
)


class RedactionStrategy(StrEnum):
    """How a bundle artifact is sanitized when the bundle is redacted.

    These name the three historical sanitizers so the per-artifact policy
    below declares *which* strategy each artifact kind uses, instead of
    :func:`create_diagnostic_bundle` choosing one ad hoc per member.
    """

    NONE = "none"
    PATH_FIELDS = "path_fields"
    PATH_TOKENS = "path_tokens"
    LOG_LINES = "log_lines"


# The bundle's redaction policy: every JSON member it writes names exactly one
# strategy, so a new artifact kind cannot ship without deciding how it is
# sanitized. The log family is a set of text members rather than one JSON
# member, so it declares its strategy separately.
ARTIFACT_REDACTION: Mapping[str, RedactionStrategy] = {
    "hardware.json": RedactionStrategy.PATH_FIELDS,
    "dependencies.json": RedactionStrategy.NONE,
    "graph.json": RedactionStrategy.PATH_FIELDS,
    "engine_metrics.json": RedactionStrategy.PATH_TOKENS,
    "node_profiles.json": RedactionStrategy.PATH_TOKENS,
    "manifest.json": RedactionStrategy.NONE,
}
LOG_REDACTION = RedactionStrategy.LOG_LINES


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
            hardware_data = _redact_artifact(
                "hardware.json", asdict(hardware_snapshot), include_paths
            )
            _write_json(archive, "hardware.json", hardware_data, included)
            dependencies_data = _redact_artifact(
                "dependencies.json", dependency_versions(), include_paths
            )
            _write_json(archive, "dependencies.json", dependencies_data, included)
            graph_data = _redact_artifact("graph.json", graph_to_data(graph), include_paths)
            _write_json(archive, "graph.json", graph_data, included)
            metrics_data = _redact_artifact(
                "engine_metrics.json",
                {} if engine_metrics is None else asdict(engine_metrics),
                include_paths,
            )
            _write_json(archive, "engine_metrics.json", metrics_data, included)
            profiles_data = _redact_artifact(
                "node_profiles.json", [asdict(profile) for profile in node_profiles], include_paths
            )
            _write_json(archive, "node_profiles.json", profiles_data, included)
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
            manifest_data = _redact_artifact("manifest.json", manifest, include_paths)
            _write_json(archive, "manifest.json", manifest_data, included)
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
    """Return a JSON-shaped copy with path fields and absolute path strings removed.

    This is the :attr:`RedactionStrategy.PATH_FIELDS` sanitizer, the strategy the
    bundle applies to its hardware and graph members. It is exported as the escape
    hatch for redacting path-shaped keys and absolute values from arbitrary JSON; the
    diagnostic bundle's per-artifact selection lives in ``ARTIFACT_REDACTION``.
    """

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
        if not include_paths and LOG_REDACTION is RedactionStrategy.LOG_LINES:
            text = _redact_log_lines(text)
        logs.append((f"logs/{index:02d}-{path.name}", text))
    return tuple(logs)


def _is_log_file(path: Path) -> bool:
    name = path.name.casefold()
    return bool(re.search(r"\.(?:log|txt|jsonl)(?:\.\d+)?$", name))


def _redact_log_lines(text: str) -> str:
    # Redact from an absolute-path marker to end-of-line. The markers cover
    # drive-letter paths, UNC shares, and POSIX absolute paths so network
    # shares are covered too. Over-redacting trailing diagnostic text is
    # preferable to leaking an unquoted path containing spaces.
    return re.sub(r"(?i)(?:[A-Z]:\\|\\\\[A-Za-z0-9]|/)[^\r\n]*", REDACTED, text)


def _redact_path_tokens(value: object) -> object:
    """Return a JSON-shaped copy with path-like substrings replaced by REDACTED."""

    if isinstance(value, str):
        return _PATH_TOKEN_PATTERN.sub(REDACTED, value)
    if isinstance(value, Mapping):
        items = cast("Mapping[object, object]", value)
        return {str(key): _redact_path_tokens(item) for key, item in items.items()}
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
        return [_redact_path_tokens(item) for item in items]
    return value


def _redact_artifact(name: str, value: object, include_paths: bool) -> object:
    """Apply ``name``'s declared redaction strategy unless paths are kept.

    The strategy lookup runs even when paths are kept, so a member the bundle
    writes but never declared in ``ARTIFACT_REDACTION`` raises here instead of
    leaking unredacted.
    """
    strategy = ARTIFACT_REDACTION[name]
    if include_paths or strategy is RedactionStrategy.NONE:
        return value
    if strategy is RedactionStrategy.PATH_FIELDS:
        return redact_sensitive_paths(value)
    if strategy is RedactionStrategy.PATH_TOKENS:
        return _redact_path_tokens(value)
    return value


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
    "ARTIFACT_REDACTION",
    "LOG_REDACTION",
    "DiagnosticBundleResult",
    "RedactionStrategy",
    "create_diagnostic_bundle",
    "dependency_versions",
    "redact_sensitive_paths",
]
