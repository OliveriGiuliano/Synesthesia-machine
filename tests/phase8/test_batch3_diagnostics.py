"""Phase 8 batch 3 hardware collection and privacy-aware diagnostic bundles."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from synesthesia_machine.contracts import EngineMetrics, EngineState
from synesthesia_machine.diagnostics import (
    NvidiaSnapshot,
    collect_hardware_snapshot,
    create_diagnostic_bundle,
    redact_sensitive_paths,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence.media_relink import MEDIA_ABSOLUTE_FALLBACK_KEY


class _WorkingNvidiaAdapter:
    def snapshot(self) -> NvidiaSnapshot:
        return NvidiaSnapshot(
            True,
            device_name="Test GPU",
            vram_total_bytes=8 * 1024**3,
            vram_used_bytes=2 * 1024**3,
            utilization_percent=25.0,
        )


class _BrokenNvidiaAdapter:
    def snapshot(self) -> NvidiaSnapshot:
        raise RuntimeError("driver unavailable")


def test_hardware_snapshot_exposes_optional_nvidia_adapter_states() -> None:
    unavailable = collect_hardware_snapshot()
    working = collect_hardware_snapshot(_WorkingNvidiaAdapter())
    broken = collect_hardware_snapshot(_BrokenNvidiaAdapter())

    assert unavailable.memory_total_bytes > 0
    assert unavailable.process_rss_bytes > 0
    assert not unavailable.nvidia.available
    assert working.nvidia.available
    assert working.nvidia.device_name == "Test GPU"
    assert not broken.nvidia.available
    assert broken.nvidia.reason is not None and "driver unavailable" in broken.nvidia.reason


def test_recursive_redaction_covers_path_fields_and_absolute_values() -> None:
    source = {
        "file_path": r"C:\Users\artist\private\movie.mp4",
        "nested": {MEDIA_ABSOLUTE_FALLBACK_KEY: "/home/artist/movie.mp4"},
        "ordinary": "keep me",
        "absolute_without_path_key": r"D:\captures\take.mov",
    }

    redacted = redact_sensitive_paths(source)

    assert isinstance(redacted, dict)
    assert redacted["file_path"] == "<redacted>"
    assert redacted["nested"] == {MEDIA_ABSOLUTE_FALLBACK_KEY: "<redacted>"}
    assert redacted["ordinary"] == "keep me"
    assert redacted["absolute_without_path_key"] == "<redacted>"


def test_bundle_is_bounded_redacted_and_contains_no_frames(tmp_path: Path) -> None:
    media_path = tmp_path / "private media" / "source file.mp4"
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(media_path)},
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    for index in range(7):
        (logs / f"engine-{index}.log").write_text(
            f"opened {media_path} at tick {index}\n", encoding="utf-8"
        )
    (logs / "frame.png").write_bytes(b"not a real frame")
    (logs / "synesthesia-machine-engine.jsonl").write_text(
        '{"message":"engine failure"}\n', encoding="utf-8"
    )
    (logs / "synesthesia-machine-ui.jsonl.1").write_text(
        '{"message":"rotated UI log"}\n', encoding="utf-8"
    )
    output = tmp_path / "diagnostics.zip"

    result = create_diagnostic_bundle(
        output,
        document.snapshot(),
        logs_directory=logs,
        engine_metrics=EngineMetrics(state=EngineState.RUNNING, processed_ticks=42),
    )

    assert result.redacted
    assert result.log_files_included == 5
    with ZipFile(output) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        graph = archive.read("graph.json").decode("utf-8")
        metrics = json.loads(archive.read("engine_metrics.json"))
        log_text = archive.read(
            sorted(name for name in names if name.startswith("logs/"))[0]
        ).decode()
    assert manifest["frames_included"] is False
    assert not any(name.endswith((".png", ".jpg", ".mp4")) for name in names)
    assert str(media_path) not in graph
    assert "<redacted>" in graph
    assert str(media_path) not in log_text
    assert metrics["state"] == "RUNNING"
    assert metrics["processed_ticks"] == 42


def test_bundle_can_include_paths_only_when_explicitly_requested(tmp_path: Path) -> None:
    media_path = tmp_path / "source.mp4"
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(media_path)},
    )
    output = tmp_path / "diagnostics-with-paths.zip"

    result = create_diagnostic_bundle(output, document.snapshot(), include_paths=True)

    with ZipFile(output) as archive:
        graph = archive.read("graph.json").decode("utf-8")
    assert not result.redacted
    assert str(media_path).replace("\\", "\\\\") in graph
