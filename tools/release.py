"""Deterministic release inventory, provenance, and archive tooling."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import av
from packaging.requirements import Requirement
from packaging.utils import NormalizedName, canonicalize_name

from synesthesia_machine import __version__

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_FILE = REPOSITORY_ROOT / "pyproject.toml"
LOCK_FILE = REPOSITORY_ROOT / "uv.lock"
DEPLOY_SPEC = REPOSITORY_ROOT / "pysidedeploy.spec"
VERSION_FILE = REPOSITORY_ROOT / "src" / "synesthesia_machine" / "version.py"
LICENSE_PREFIXES = ("license", "licence", "copying", "notice")
NATIVE_DISTRIBUTIONS = ("av", "opencv-python", "python-rtmidi", "sounddevice")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _project_version() -> str:
    data = tomllib.loads(PROJECT_FILE.read_text(encoding="utf-8"))
    project = cast("Mapping[str, object]", data["project"])
    return str(project["version"])


def _source_version() -> str:
    match = re.search(
        r'^__version__\s*=\s*"([^"]+)"\s*$',
        VERSION_FILE.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"Could not read __version__ from {VERSION_FILE}")
    return match.group(1)


def check_release_configuration() -> None:
    project_version = _project_version()
    source_version = _source_version()
    parser = configparser.ConfigParser()
    parser.read(DEPLOY_SPEC, encoding="utf-8")
    mode = parser.get("nuitka", "mode")
    extra_args = parser.get("nuitka", "extra_args")
    expected_windows_version = f"{project_version}.0"
    expected_tokens = (
        "--windows-console-mode=disable",
        f"--file-version={expected_windows_version}",
        f"--product-version={project_version}",
        "--include-package=synesthesia_machine",
    )
    failures: list[str] = []
    if __version__ != project_version or source_version != project_version:
        failures.append(
            "version mismatch: "
            f"import={__version__}, pyproject={project_version}, source={source_version}"
        )
    if mode != "standalone":
        failures.append(f"Nuitka mode must be standalone, found {mode!r}")
    for token in expected_tokens:
        if token not in extra_args:
            failures.append(f"missing Nuitka release option: {token}")
    icon = (REPOSITORY_ROOT / parser.get("app", "icon")).resolve()
    if not icon.is_file():
        failures.append(f"application icon does not exist: {icon}")
    if not LOCK_FILE.is_file():
        failures.append("uv.lock is missing")
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        failures.append(
            f"release builds require Windows x64, found {platform.system()} {platform.machine()}"
        )
    if failures:
        raise RuntimeError("; ".join(failures))


def _requirement_names(
    distribution: importlib.metadata.Distribution,
) -> tuple[NormalizedName, ...]:
    names: set[NormalizedName] = set()
    for raw_requirement in distribution.requires or ():
        requirement = Requirement(raw_requirement)
        if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
            continue
        names.add(canonicalize_name(requirement.name))
    return tuple(sorted(names))


def _runtime_distributions() -> tuple[importlib.metadata.Distribution, ...]:
    pending = deque([canonicalize_name("synesthesia-machine")])
    discovered: dict[NormalizedName, importlib.metadata.Distribution] = {}
    while pending:
        name = pending.popleft()
        if name in discovered:
            continue
        distribution = importlib.metadata.distribution(name)
        discovered[name] = distribution
        pending.extend(item for item in _requirement_names(distribution) if item not in discovered)
    discovered.pop(canonicalize_name("synesthesia-machine"), None)
    return tuple(discovered[name] for name in sorted(discovered))


def _license_files(
    distribution: importlib.metadata.Distribution,
) -> tuple[tuple[str, Path], ...]:
    results: list[tuple[str, Path]] = []
    for package_path in distribution.files or ():
        relative = str(package_path).replace("\\", "/")
        filename = PurePosixPath(relative).name.casefold()
        portaudio_notice = "portaudio-binaries/readme" in relative.casefold()
        if not filename.startswith(LICENSE_PREFIXES) and not portaudio_notice:
            continue
        located = Path(str(distribution.locate_file(package_path))).resolve()
        if located.is_file():
            results.append((relative, located))
    return tuple(sorted(results, key=lambda item: item[0].casefold()))


def _metadata_value(distribution: importlib.metadata.Distribution, key: str) -> str:
    value = distribution.metadata.get(key)
    return "" if value is None else str(value).strip()


def _project_urls(distribution: importlib.metadata.Distribution) -> tuple[str, ...]:
    values = distribution.metadata.get_all("Project-URL") or ()
    return tuple(sorted(str(value) for value in values))


def _copy_license_files(
    distributions: Sequence[importlib.metadata.Distribution], destination: Path
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for distribution in distributions:
        name = canonicalize_name(_metadata_value(distribution, "Name"))
        package_destination = destination / name
        package_destination.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        for relative, source in _license_files(distribution):
            base = PurePosixPath(relative).name
            candidate = base
            index = 2
            while candidate.casefold() in used:
                candidate = f"{Path(base).stem}-{index}{Path(base).suffix}"
                index += 1
            used.add(candidate.casefold())
            shutil.copyfile(source, package_destination / candidate)


def _native_files(
    distributions: Sequence[importlib.metadata.Distribution],
) -> tuple[dict[str, object], ...]:
    selected = {canonicalize_name(name) for name in NATIVE_DISTRIBUTIONS}
    records: list[dict[str, object]] = []
    for distribution in distributions:
        distribution_name = canonicalize_name(_metadata_value(distribution, "Name"))
        if distribution_name not in selected:
            continue
        for package_path in distribution.files or ():
            relative = str(package_path).replace("\\", "/")
            lower = relative.casefold()
            if not lower.endswith((".dll", ".pyd")):
                continue
            if distribution_name == "av" and not lower.startswith(("av", "av.libs")):
                continue
            if distribution_name == "opencv-python" and not lower.startswith("cv2"):
                continue
            if distribution_name == "python-rtmidi" and "rtmidi" not in lower:
                continue
            if distribution_name == "sounddevice" and not lower.startswith(
                ("sounddevice", "_sounddevice_data")
            ):
                continue
            located = Path(str(distribution.locate_file(package_path))).resolve()
            if located.is_file():
                records.append(
                    {
                        "distribution": distribution_name,
                        "path": relative,
                        "sha256": _sha256(located),
                    }
                )
    return tuple(sorted(records, key=lambda item: (str(item["distribution"]), str(item["path"]))))


def _ffmpeg_runtime() -> dict[str, object]:
    library_versions = {
        str(name): list(version) for name, version in sorted(av.library_versions.items())
    }
    codec_module = importlib.import_module("av.codec")
    codec_values = cast("Iterable[object]", codec_module.codecs_available)
    codecs = sorted(str(codec) for codec in codec_values)
    return {
        "provider": "PyAV wheel",
        "library_versions": library_versions,
        "available_codecs": codecs,
        "build_configuration_exposed_by_runtime": False,
        "release_gate": "legal review required before external distribution",
    }


def dependency_inventory() -> tuple[dict[str, object], tuple[importlib.metadata.Distribution, ...]]:
    distributions = _runtime_distributions()
    packages: list[dict[str, object]] = []
    for distribution in distributions:
        name = canonicalize_name(_metadata_value(distribution, "Name"))
        license_expression = _metadata_value(distribution, "License-Expression")
        legacy_license = _metadata_value(distribution, "License")
        license_records = [
            {"path": relative, "sha256": _sha256(path)}
            for relative, path in _license_files(distribution)
        ]
        packages.append(
            {
                "name": name,
                "version": distribution.version,
                "license_expression": license_expression or None,
                "legacy_license": legacy_license or None,
                "project_urls": list(_project_urls(distribution)),
                "requires": list(_requirement_names(distribution)),
                "license_files": license_records,
            }
        )
    payload: dict[str, object] = {
        "schema_version": 1,
        "application": {
            "name": "synesthesia-machine",
            "version": __version__,
            "distribution_permission": "not granted; see LICENSE-or-NOTICE.md",
        },
        "source_inputs": {
            "pyproject_sha256": _sha256(PROJECT_FILE),
            "uv_lock_sha256": _sha256(LOCK_FILE),
        },
        "packages": packages,
        "native_files": list(_native_files(distributions)),
        "ffmpeg_runtime": _ffmpeg_runtime(),
    }
    return payload, distributions


def write_notices(path: Path, inventory: Mapping[str, object]) -> None:
    packages = cast("Sequence[Mapping[str, object]]", inventory["packages"])
    lines = [
        "# Third-party notices",
        "",
        f"This inventory accompanies Synesthesia Machine {__version__}. It is generated from the",
        "exact locked Windows build environment and is not a substitute for legal review. The",
        "release directory includes the licence files reported below under `licenses/`.",
        "",
        "External distribution remains blocked by `LICENSE-or-NOTICE.md` and",
        "`packaging/licensing-review.md` until the application licence/distribution model and",
        "native codec obligations are approved.",
        "",
        "| Distribution | Version | Declared licence metadata | Bundled licence files |",
        "| --- | --- | --- | --- |",
    ]
    for package in packages:
        expression = package.get("license_expression") or package.get("legacy_license") or "unknown"
        license_files = cast("Sequence[Mapping[str, object]]", package["license_files"])
        file_names = (
            ", ".join(
                f"`{name}`"
                for name in sorted(
                    {PurePosixPath(str(item["path"])).name for item in license_files},
                    key=str.casefold,
                )
            )
            or "none reported"
        )
        metadata = str(expression).replace("|", "\\|").replace("\n", " ")
        if len(metadata) > 120:
            metadata = "legacy metadata contains full licence text; see bundled licence file"
        lines.append(f"| {package['name']} | {package['version']} | {metadata} | {file_names} |")
    lines.extend(
        [
            "",
            "The machine-readable dependency versions, native binary hashes, FFmpeg library",
            "versions, and available codec names are in `dependency-inventory.json`.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def create_inventory(output: Path, notices: Path, licenses: Path | None) -> None:
    inventory, distributions = dependency_inventory()
    _write_json(output, inventory)
    write_notices(notices, inventory)
    if licenses is not None:
        _copy_license_files(distributions, licenses)


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _artifact_files(root: Path, excluded: Iterable[Path] = ()) -> tuple[Path, ...]:
    excluded_resolved = {path.resolve() for path in excluded}
    return tuple(
        path
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold())
        if path.is_file() and path.resolve() not in excluded_resolved
    )


def create_provenance(artifact: Path, output: Path) -> None:
    artifact = artifact.resolve()
    output = output.resolve()
    source_status = _git("status", "--porcelain", "--untracked-files=all")
    payload = {
        "schema_version": 1,
        "application_version": __version__,
        "source": {
            "commit": _git("rev-parse", "HEAD"),
            "dirty": bool(source_status),
            "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH", ""),
            "pyproject_sha256": _sha256(PROJECT_FILE),
            "uv_lock_sha256": _sha256(LOCK_FILE),
            "pysidedeploy_spec_sha256": _sha256(DEPLOY_SPEC),
        },
        "toolchain": {
            "python": platform.python_version(),
            "pyside6": importlib.metadata.version("pyside6"),
            "nuitka": importlib.metadata.version("nuitka"),
            "uv": _uv_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "artifact": {
            "directory": artifact.name,
            "files": [
                {
                    "path": path.relative_to(artifact).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in _artifact_files(artifact, (output,))
            ],
        },
    }
    _write_json(output, payload)


def _uv_version() -> str:
    result = subprocess.run(
        ["uv", "--version"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def create_deterministic_archive(artifact: Path, output: Path) -> None:
    artifact = artifact.resolve()
    output = output.resolve()
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))
    timestamp = time.gmtime(max(epoch, 315532800))[:6]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for path in _artifact_files(artifact):
                relative = PurePosixPath(artifact.name) / path.relative_to(artifact).as_posix()
                info = ZipInfo(str(relative), date_time=timestamp)
                info.compress_type = ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=ZIP_DEFLATED,
                    compresslevel=9,
                )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate versions and deployment invariants")
    inventory = commands.add_parser("inventory", help="write runtime inventory and notices")
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument("--notices", type=Path, required=True)
    inventory.add_argument("--licenses", type=Path)
    provenance = commands.add_parser("provenance", help="hash a standalone artifact")
    provenance.add_argument("--artifact", type=Path, required=True)
    provenance.add_argument("--output", type=Path, required=True)
    archive = commands.add_parser("archive", help="create a sorted fixed-timestamp ZIP")
    archive.add_argument("--artifact", type=Path, required=True)
    archive.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    command = cast("str", arguments.command)
    if command == "check":
        check_release_configuration()
    elif command == "inventory":
        create_inventory(arguments.output, arguments.notices, arguments.licenses)
    elif command == "provenance":
        create_provenance(arguments.artifact, arguments.output)
    elif command == "archive":
        create_deterministic_archive(arguments.artifact, arguments.output)
    else:
        raise AssertionError(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
