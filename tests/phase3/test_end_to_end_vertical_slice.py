"""Saved Phase 3 graph and generated video-to-MIDI integration tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import UUID

from tools.generate_test_video import HUE_FRAME_COUNT, generate_hue_test_video

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import MidiStateFrame, NoteActivity, SourceState
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.midi import SynthConfiguration
from synesthesia_machine.nodes.output import GENERATE_AUDIO_TYPE_ID
from synesthesia_machine.persistence import load_graph, save_graph
from synesthesia_machine.runtime import InProcessEngineClient

EXAMPLE_PATH = Path("examples/phase3/hue_chord.synmachine.json")
SOURCE_ID = UUID("30000000-0000-0000-0000-000000000001")
RESIZE_ID = UUID("30000000-0000-0000-0000-000000000002")
COLOUR_ID = UUID("30000000-0000-0000-0000-000000000003")
CHANNELS_ID = UUID("30000000-0000-0000-0000-000000000004")
NOTE_VISUALIZER_ID = UUID("30000000-0000-0000-0000-000000000007")
AUDIO_ID = UUID("30000000-0000-0000-0000-000000000008")
EXPECTED_NOTES = (60, 62, 64, 65, 67, 69, 71)


class RecordingSynth:
    def __init__(self, configuration: SynthConfiguration) -> None:
        self.configuration = configuration
        self.updates: list[MidiStateFrame] = []
        self.panic_count = 0
        self.close_count = 0

    def update(self, state: MidiStateFrame) -> None:
        self.updates.append(state)

    def panic(self) -> None:
        self.panic_count += 1

    def close(self) -> None:
        self.close_count += 1


class RecordingSynthFactory:
    def __init__(self) -> None:
        self.synths: list[RecordingSynth] = []

    def __call__(self, configuration: SynthConfiguration) -> RecordingSynth:
        synth = RecordingSynth(configuration)
        self.synths.append(synth)
        return synth


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


def test_saved_example_is_portable_complete_and_audio_opt_in(tmp_path: Path) -> None:
    assert (EXAMPLE_PATH.parent / "media" / "hue_chord_demo.mp4").is_file()
    graph_path = _materialize_example(tmp_path / "portable")
    registry = create_application_registry()

    snapshot = load_graph(graph_path, registry)

    assert {node.type_id for node in snapshot.nodes} == {
        "synmachine.input.load_video",
        "synmachine.image.resize",
        "synmachine.image.change_colour_space",
        "synmachine.image.separate_channels",
        "synmachine.synesthesia.channel_to_pitch",
        "synmachine.visualization.display_image_data",
        "synmachine.visualization.note_visualizer",
        "synmachine.output.generate_audio",
    }
    assert len(snapshot.connections) == 7
    source = snapshot.node(SOURCE_ID)
    audio = snapshot.node(AUDIO_ID)
    assert source is not None
    assert (
        Path(str(source.parameters["file_path"]))
        == (graph_path.parent / "media" / "hue_chord_demo.mp4").resolve()
    )
    assert audio is not None and audio.type_id == GENERATE_AUDIO_TYPE_ID
    assert audio.parameters["enabled"] is False

    round_trip = graph_path.with_name("round_trip.synmachine.json")
    save_graph(round_trip, snapshot)
    data = json.loads(round_trip.read_text(encoding="utf-8"))
    persisted_source = next(node for node in data["nodes"] if node["id"] == str(SOURCE_ID))
    assert Path(persisted_source["parameters"]["file_path"]) == Path("media/hue_chord_demo.mp4")


def test_generated_video_drives_expected_midi_previews_and_mock_audio_via_client(
    tmp_path: Path,
) -> None:
    graph_path = _materialize_example(tmp_path / "runtime")
    synth_factory = RecordingSynthFactory()
    registry = create_application_registry(synth_factory=synth_factory)
    document = GraphDocument.from_snapshot(load_graph(graph_path, registry))
    document.set_parameter(SOURCE_ID, "loop", False)
    document.set_parameter(AUDIO_ID, "enabled", True)
    client = InProcessEngineClient(registry)
    try:
        activation = client.activate(document.snapshot())
        assert activation.activated and activation.report.is_valid
        assert client.source_status(SOURCE_ID)[0].state is SourceState.READY

        client.play(SOURCE_ID)
        assert client.wait_until_idle(3.0)

        status = client.source_status(SOURCE_ID)[0]
        metrics = client.metrics()
        assert status.state is SourceState.ENDED
        assert status.processed_index == HUE_FRAME_COUNT
        assert metrics.processed_ticks == HUE_FRAME_COUNT
        assert metrics.dropped_before_processing == 0
        assert len(synth_factory.synths) == 1
        synth = synth_factory.synths[0]
        rendered_states = tuple(
            tuple((key.channel, key.note, velocity) for key, velocity in state.notes.items())
            for state in synth.updates
        )
        assert rendered_states == tuple(((0, note, 100),) for note in EXPECTED_NOTES)
        assert synth.panic_count == 1

        image_previews = client.poll_image_previews()
        note_previews = client.poll_note_previews()
        # Image previews are anchored on each demanded producer, not on the
        # display node, so every producer with a connected image/channel output
        # (source, resize, colour, separate_channels) owns a link-pill preview.
        by_owner = {preview.owner_id: preview for preview in image_previews}
        assert set(by_owner) == {SOURCE_ID, RESIZE_ID, COLOUR_ID, CHANNELS_ID}
        assert (by_owner[RESIZE_ID].width, by_owner[RESIZE_ID].height) == (500, 500)
        assert len(note_previews) == 1
        assert note_previews[0].owner_id == NOTE_VISUALIZER_ID
        assert note_previews[0].tick_index == HUE_FRAME_COUNT
        assert note_previews[0].notes == (NoteActivity(0, EXPECTED_NOTES[-1], 100),)

        client.panic()
        assert synth.panic_count == 2
    finally:
        client.close()

    assert synth_factory.synths[0].close_count == 1
