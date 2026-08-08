"""Standalone configuration, inventory, smoke, and deterministic archive contracts."""

from __future__ import annotations

import configparser
import hashlib
import json
import struct
from pathlib import Path

import pytest
from tools import phase9_release

from synesthesia_machine import __version__
from synesthesia_machine.app import bootstrap, release_smoke

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_configuration_is_standalone_versioned_and_console_free() -> None:
    phase9_release.check_release_configuration()
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pysidedeploy.spec", encoding="utf-8")

    assert parser.get("nuitka", "mode") == "standalone"
    assert parser.get("app", "input_file") == "SynesthesiaMachine.py"
    assert parser.get("python", "packages") == "Nuitka==4.1"
    extra_args = parser.get("nuitka", "extra_args")
    assert "--windows-console-mode=disable" in extra_args
    assert "--include-package=av" in extra_args
    assert f"--product-version={__version__}" in extra_args
    assert f"--file-version={__version__}.0" in extra_args
    assert "--onefile" not in extra_args
    assert "--include-package-data=sounddevice" not in extra_args
    assert "--no-prefer-source-code" in extra_args
    assert "--noinclude-dlls=*asio*.dll" in extra_args


def test_windows_icon_contains_all_required_resolutions() -> None:
    icon_path = ROOT / "src/synesthesia_machine/resources/synesthesia-machine.ico"
    payload = icon_path.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", payload)
    assert (reserved, image_type, count) == (0, 1, 7)
    widths: set[int] = set()
    heights: set[int] = set()
    for index in range(count):
        width, height = struct.unpack_from("<BB", payload, 6 + index * 16)
        widths.add(256 if width == 0 else width)
        heights.add(256 if height == 0 else height)
    assert widths == heights == {16, 24, 32, 48, 64, 128, 256}


def test_bootstrap_removes_release_only_arguments_from_qt() -> None:
    arguments = [
        "SynesthesiaMachine.exe",
        "--packaged-smoke-report",
        "report.json",
        "--h264-video",
        "video.mp4",
        "--smoke-test",
        "-platform",
        "offscreen",
    ]
    assert bootstrap._option_value(arguments, "--h264-video") == "video.mp4"  # pyright: ignore[reportPrivateUsage]
    assert bootstrap._qt_arguments(arguments) == [  # pyright: ignore[reportPrivateUsage]
        "SynesthesiaMachine.exe",
        "-platform",
        "offscreen",
    ]


def test_packaged_smoke_report_fails_closed_and_records_skips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(release_smoke, "_graph_round_trip", lambda _root: "graph ok")
    monkeypatch.setattr(release_smoke, "_decode_h264", lambda _path: "h264 ok")
    monkeypatch.setattr(
        release_smoke,
        "_camera_probe",
        lambda: ("skipped", "no camera"),
    )
    monkeypatch.setattr(release_smoke, "_midi_probe", lambda: "midi ok")
    monkeypatch.setattr(release_smoke, "_audio_probe", lambda: "audio ok")
    monkeypatch.setattr(release_smoke, "_engine_restart", lambda _root: "engine ok")
    monkeypatch.setattr(
        release_smoke,
        "_autosave_and_diagnostics",
        lambda _root: "recovery ok",
    )
    report_path = tmp_path / "report.json"

    assert release_smoke.run_packaged_smoke(report_path, h264_video=tmp_path / "video.mp4") == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert {item["status"] for item in report["checks"]} == {"passed", "skipped"}

    assert release_smoke.run_packaged_smoke(report_path) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert (
        next(item for item in report["checks"] if item["name"] == "h264_mp4_decode")["status"]
        == "failed"
    )


def test_inventory_covers_locked_native_runtime_and_licence_files() -> None:
    inventory, _ = phase9_release.dependency_inventory()
    packages = {item["name"]: item for item in inventory["packages"]}
    assert {
        "av",
        "mido",
        "numpy",
        "opencv-python",
        "psutil",
        "pyside6",
        "python-rtmidi",
        "sounddevice",
    } <= packages.keys()
    assert all(item["version"] for item in packages.values())
    assert any(item["license_files"] for item in packages.values())
    native_distributions = {item["distribution"] for item in inventory["native_files"]}
    assert {"av", "opencv-python", "python-rtmidi", "sounddevice"} <= native_distributions
    ffmpeg = inventory["ffmpeg_runtime"]
    assert "h264" in ffmpeg["available_codecs"]
    assert ffmpeg["build_configuration_exposed_by_runtime"] is False


def test_archive_is_byte_reproducible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "Synesthesia Machine.dist"
    (artifact / "nested").mkdir(parents=True)
    (artifact / "z.txt").write_text("last\n", encoding="utf-8")
    (artifact / "nested/a.txt").write_text("first\n", encoding="utf-8")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    phase9_release.create_deterministic_archive(artifact, first)
    phase9_release.create_deterministic_archive(artifact, second)

    assert _sha256(first) == _sha256(second)


def test_build_and_clean_machine_scripts_enforce_release_boundaries() -> None:
    build = (ROOT / "packaging/build.ps1").read_text(encoding="utf-8")
    smoke = (ROOT / "packaging/smoke_test.ps1").read_text(encoding="utf-8")
    assert "uv sync --locked --group packaging" in build
    assert "pyside6-deploy" in build
    assert "tools.phase9_release provenance" in build
    assert "Assert-NativeSuccess -Operation 'static checks'" in build
    assert "libportaudio64bit.dll" in build
    assert "--packaged-smoke-report" in smoke
    assert "'*rtmidi*.pyd'" in smoke
    assert 'PATH\'] = "$env:SystemRoot\\System32;$env:SystemRoot"' in smoke
    assert "Remove-Item -LiteralPath $portableCopy" in smoke
    assert "documentSentinel" in smoke
