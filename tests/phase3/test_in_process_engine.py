"""In-process EngineClient source orchestration and graph-mailbox tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.generate_test_video import DEFAULT_FRAME_COUNT, generate_test_video

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import EngineState, SourceState
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.runtime import InProcessEngineClient


def test_client_activates_real_video_and_drives_graph_through_final_api(tmp_path: Path) -> None:
    video = generate_test_video(tmp_path / "client.mp4", frame_count=DEFAULT_FRAME_COUNT, fps=60)
    registry = create_application_registry()
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(video)},
    )
    resize_id = document.add_node(
        "synmachine.image.resize",
        parameters={"width": 32, "height": 24},
    )
    document.add_connection(source_id, "image", resize_id, "image")
    client = InProcessEngineClient(registry)
    try:
        activation = client.activate(document.snapshot())
        assert activation.activated and activation.report.is_valid
        assert client.source_status(source_id)[0].state is SourceState.READY

        client.play(source_id)
        assert client.wait_until_idle(2.0)

        status = client.source_status(source_id)[0]
        metrics = client.metrics()
        assert status.state is SourceState.ENDED
        assert status.processed_index == DEFAULT_FRAME_COUNT
        assert metrics.state is EngineState.STOPPED
        assert metrics.processed_ticks == DEFAULT_FRAME_COUNT
        assert metrics.memory_bytes > 0
    finally:
        client.close()


def test_invalid_source_path_reports_error_without_rejecting_valid_graph(tmp_path: Path) -> None:
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(tmp_path / "missing.mp4")},
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        status = client.source_status(source_id)[0]
        assert status.state is SourceState.ERROR
        assert status.last_error is not None
        assert client.metrics().state is EngineState.ERROR
        with pytest.raises(RuntimeError):
            client.play(source_id)
    finally:
        client.close()


def test_seek_is_explicitly_reserved(tmp_path: Path) -> None:
    video = generate_test_video(tmp_path / "seek.mp4", frame_count=2)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        with pytest.raises(NotImplementedError, match="reserved"):
            client.seek(source_id, 0.5)
    finally:
        client.close()
