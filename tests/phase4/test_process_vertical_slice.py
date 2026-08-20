"""Spawned-engine acceptance evidence for the canonical Hue Chord graph."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from uuid import UUID

import numpy as np
import pytest
from tools.generate_test_video import HUE_FRAME_COUNT, generate_hue_test_video

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import NoteActivity, SourceState
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import load_graph
from synesthesia_machine.runtime import ProcessEngineClient

EXAMPLE_PATH = Path("examples/phase3/hue_chord.synmachine.json")
SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
RESIZE_ID = UUID("30000000-0000-0000-0000-000000000002")
NOTE_VISUALIZER_ID = UUID("30000000-0000-0000-0000-000000000007")
AUDIO_ID = UUID("30000000-0000-0000-0000-000000000008")
EXPECTED_FINAL_NOTE = NoteActivity(0, 71, 100)

T = TypeVar("T")


def _materialize_example(root: Path) -> Path:
    graph_path = root / EXAMPLE_PATH.name
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_PATH, graph_path)
    generate_hue_test_video(
        graph_path.parent / "media" / "hue_chord_demo.mp4",
        width=64,
        height=64,
        frame_count=HUE_FRAME_COUNT,
        fps=12,
    )
    return graph_path


def _wait_for(
    poll: Callable[[], tuple[T, ...]],
    matches: Callable[[T], bool],
    *,
    timeout_s: float = 3.0,
) -> T:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        match = next((value for value in poll() if matches(value)), None)
        if match is not None:
            return match
        time.sleep(0.02)
    pytest.fail("Expected spawned-engine preview did not arrive before the timeout")


def test_canonical_hue_chord_runs_in_child_with_audio_opt_in_and_bounded_previews(
    tmp_path: Path,
) -> None:
    graph_path = _materialize_example(tmp_path / "spawned-runtime")
    registry = create_application_registry()
    document = GraphDocument.from_snapshot(load_graph(graph_path, registry))
    document.set_parameter(SOURCE_ID, "loop", False)
    audio = document.node(AUDIO_ID)
    assert audio is not None and audio.parameters["enabled"] is False

    client = ProcessEngineClient(
        request_timeout_s=1.5,
        activation_timeout_s=5.0,
        heartbeat_timeout_s=1.5,
        close_timeout_s=0.5,
    )
    try:
        status = client.status()
        assert status.child_process_id is not None
        assert status.child_process_id != os.getpid()

        activation = client.activate(document.snapshot())
        assert activation.activated and activation.report.is_valid
        assert client.source_status(SOURCE_ID)[0].state is SourceState.READY

        client.play(SOURCE_ID)
        assert client.wait_until_idle(3.0)

        source = client.source_status(SOURCE_ID)[0]
        metrics = client.metrics()
        assert source.state is SourceState.ENDED
        assert source.processed_index == HUE_FRAME_COUNT
        assert metrics.child_process_id == status.child_process_id
        assert metrics.processed_ticks == HUE_FRAME_COUNT
        assert metrics.dropped_before_processing == 0

        image = _wait_for(
            client.poll_image_previews,
            lambda preview: preview.owner_id == RESIZE_ID,
        )
        note = _wait_for(
            client.poll_note_previews,
            lambda preview: preview.owner_id == NOTE_VISUALIZER_ID,
        )

        assert (image.width, image.height, image.channels) == (500, 500, 3)
        assert image.data.dtype == np.uint8
        assert not image.data.flags.writeable
        assert image.tick_index == HUE_FRAME_COUNT
        # One shared-memory slot per connected image/channel output port. The four
        # demanded producers (source, resize, colour, separate_channels) each expose
        # exactly one connected such port; display nodes own none.
        assert len(client.preview_shared_memory_names()) == 4
        assert note.tick_index == HUE_FRAME_COUNT
        assert note.notes == (EXPECTED_FINAL_NOTE,)
    finally:
        client.close()
