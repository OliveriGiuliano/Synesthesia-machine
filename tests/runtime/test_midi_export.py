"""Offline MIDI export: full simulation, error codes, cancellation, progress."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from tools.generate_test_video import HUE_FRAME_COUNT, generate_hue_test_video

from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.midi import NullDebugSynth, NullMidiOutputService
from synesthesia_machine.nodes.composition import create_builtin_registry
from synesthesia_machine.runtime import MidiExportError, run_midi_export


@pytest.fixture
def export_env(tmp_path: Path):
    video = tmp_path / "hue.mp4"
    generate_hue_test_video(video, width=64, height=64, frame_count=HUE_FRAME_COUNT, fps=12)
    registry = create_builtin_registry(
        midi_output_service_factory=NullMidiOutputService,
        synth_factory=NullDebugSynth,
    )
    return video, registry


def _video_pitch_midi_document(video: Path) -> GraphDocument:
    document = GraphDocument()
    source = document.add_node("synmachine.input.load_video", parameters={"file_path": str(video)})
    luminance = document.add_node("synmachine.image.to_luminance")
    pitch = document.add_node("synmachine.synesthesia.channel_to_pitch")
    send = document.add_node("synmachine.output.send_midi")
    document.add_connection(source, "image", luminance, "image")
    document.add_connection(luminance, "channel", pitch, "value")
    document.add_connection(pitch, "midi", send, "midi")
    return document


def test_export_collects_every_note_event_from_start_to_end(export_env) -> None:
    video, registry = export_env
    document = _video_pitch_midi_document(video)
    progress = []
    result = run_midi_export(
        document.snapshot(),
        registry=registry,
        progress=lambda p: progress.append((p.processed, p.total, p.fraction)),
    )
    # Seven 12 fps frames end at 7/12 s; every frame changes the pitch, so the
    # export is: 7 note-ons, one note-off per frame change, plus a final
    # all-stop at the end of the video.
    assert len(result.events) == HUE_FRAME_COUNT * 2
    assert result.events[0].kind == "note_on"
    assert result.events[0].time_s == 0.0
    assert result.events[-1].kind == "note_off"
    assert abs(result.events[-1].time_s - result.duration_s) < 0.001
    times = [event.time_s for event in result.events]
    assert times == sorted(times)
    assert all(0.0 <= t <= result.duration_s for t in times)
    # Every note that sounded is eventually stopped.
    open_notes = set()
    for event in result.events:
        if event.kind == "note_on":
            open_notes.add((event.channel, event.note))
        else:
            open_notes.discard((event.channel, event.note))
    assert open_notes == set()
    assert all(event.velocity in (0, 127) for event in result.events)
    # Progress reported the full video length and reached completion.
    assert progress and progress[-1][0] == HUE_FRAME_COUNT
    assert progress[-1][1] == HUE_FRAME_COUNT
    assert progress[-1][2] == 1.0
    # The result is directly writable as a Standard MIDI File.
    assert result.to_standard_midi_file()[:4] == b"MThd"


def test_export_rejects_camera_sources(export_env) -> None:
    video, registry = export_env
    document = _video_pitch_midi_document(video)
    document.add_node("synmachine.input.load_camera")
    with pytest.raises(MidiExportError) as excinfo:
        run_midi_export(document.snapshot(), registry=registry)
    assert excinfo.value.code == "camera_source"


def test_export_requires_a_send_midi_node(export_env) -> None:
    video, registry = export_env
    document = GraphDocument()
    document.add_node("synmachine.input.load_video", parameters={"file_path": str(video)})
    with pytest.raises(MidiExportError) as excinfo:
        run_midi_export(document.snapshot(), registry=registry)
    assert excinfo.value.code == "no_midi_output"


def test_export_requires_a_source(export_env) -> None:
    _video, registry = export_env
    document = GraphDocument()
    document.add_node("synmachine.output.send_midi")
    with pytest.raises(MidiExportError) as excinfo:
        run_midi_export(document.snapshot(), registry=registry)
    assert excinfo.value.code == "no_source"


def test_export_rejects_missing_video_files(export_env) -> None:
    _video, registry = export_env
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(export_env[0].parent / "absent.mp4")},
    )
    document.add_node("synmachine.output.send_midi")
    with pytest.raises(MidiExportError) as excinfo:
        run_midi_export(document.snapshot(), registry=registry)
    assert excinfo.value.code == "missing_media"


def test_export_rejects_graphs_that_do_not_activate(export_env) -> None:
    video, registry = export_env
    document = GraphDocument()
    source = document.add_node("synmachine.input.load_video", parameters={"file_path": str(video)})
    pitch = document.add_node("synmachine.synesthesia.channel_to_pitch")
    send = document.add_node("synmachine.output.send_midi")
    # Wrong type on purpose: an image into a value input is rejected.
    document.add_connection(source, "image", pitch, "image")
    document.add_connection(pitch, "midi", send, "midi")
    with pytest.raises(MidiExportError) as excinfo:
        run_midi_export(document.snapshot(), registry=registry)
    assert excinfo.value.code == "activation_failed"


def test_export_honours_a_pre_cancelled_stop_event(export_env) -> None:
    video, registry = export_env
    stop = threading.Event()
    stop.set()
    with pytest.raises(MidiExportError) as excinfo:
        run_midi_export(
            _video_pitch_midi_document(video).snapshot(),
            registry=registry,
            stop_event=stop,
        )
    assert excinfo.value.code == "cancelled"


def test_export_rejects_looping_video_sources(export_env) -> None:
    video, registry = export_env
    document = GraphDocument()
    source = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(video), "loop": True},
    )
    send = document.add_node("synmachine.output.send_midi")
    document.add_connection(source, "image", send, "midi")

    with pytest.raises(MidiExportError) as exc:
        run_midi_export(document.snapshot(), registry=registry)
    assert exc.value.code == "looping_source"


def test_export_drains_the_worker_mailbox_before_collecting(
    export_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synesthesia_machine.runtime.midi_export as midi_export_module

    video, registry = export_env
    document = _video_pitch_midi_document(video)

    # Recording subclass: the exporter must drain the latest-frame worker
    # (client.wait_until_idle) before it collects samples and closes the
    # client, otherwise the final frame's tick can be dropped.
    real_client = midi_export_module.InProcessEngineClient
    drains: list[float] = []

    class RecordingClient(real_client):
        def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
            drains.append(timeout_s)
            return super().wait_until_idle(timeout_s)

    monkeypatch.setattr(midi_export_module, "InProcessEngineClient", RecordingClient)

    result = run_midi_export(document.snapshot(), registry=registry)
    assert drains, "exporter must drain the worker mailbox before collecting samples"
    assert result.events
