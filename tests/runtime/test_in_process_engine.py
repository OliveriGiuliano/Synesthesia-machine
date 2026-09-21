"""In-process EngineClient source orchestration and graph-mailbox tests."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from tools.generate_test_video import DEFAULT_FRAME_COUNT, generate_test_video

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    EngineState,
    EngineSupervisorFacts,
    MidiOutputConnectionState,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.media.source_config import VideoSourceConfig
from synesthesia_machine.midi import MidiServiceStatus
from synesthesia_machine.nodes.input import LOAD_VIDEO_TYPE_ID, create_input_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import InProcessEngineClient

SOURCE_A = UUID("00000000-0000-0000-0000-0000000000a1")

SOURCE_B = UUID("00000000-0000-0000-0000-0000000000b2")


def test_effective_engine_state_tracks_mixed_asynchronous_source_transitions() -> None:
    client = InProcessEngineClient(create_application_registry())
    try:
        client._body._state = EngineState.RUNNING
        assert (
            client._body._effective_state(
                (
                    SourceStatus(SOURCE_A, SourceState.ENDED),
                    SourceStatus(SOURCE_B, SourceState.PAUSED),
                ),
                None,
            )
            is EngineState.PAUSED
        )
        assert (
            client._body._effective_state(
                (
                    SourceStatus(SOURCE_A, SourceState.ENDED),
                    SourceStatus(SOURCE_B, SourceState.STOPPED),
                ),
                None,
            )
            is EngineState.STOPPED
        )
    finally:
        client.close()


def test_in_process_placement_reports_vacuous_supervisor_facts() -> None:
    """No child process exists in-process: the record is the vacuous default.

    This pins the per-placement meaning of ``child_process_id=None`` and
    ``heartbeat_age_s=0.0`` (EngineSupervisorFacts docstring): they denote
    "no such process", never a measurement.
    """

    client = InProcessEngineClient(NodeRegistry(()))
    try:
        assert client.metrics().supervisor == EngineSupervisorFacts()
    finally:
        client.close()


def test_client_activates_real_video_and_drives_graph_through_final_api(tmp_path: Path) -> None:
    video = generate_test_video(tmp_path / "client.mp4", frame_count=DEFAULT_FRAME_COUNT, fps=60)
    registry = create_application_registry()
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    resize_id = document.add_node(
        "synmachine.image.resize",
        parameters={"width": 32, "height": 24},
    )
    # Fresh nodes are stamped at the definition's current implementation version.
    preview_id = document.add_node(
        "synmachine.visualization.display_image_data", implementation_version=2
    )
    document.add_connection(source_id, "image", resize_id, "image")
    document.add_connection(resize_id, "image", preview_id, "image")
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
        previews = client.next_image_previews()
        # Image previews are anchored on each producing port (source.image,
        # resize.image), not on the display node, so both producer ports own one.
        by_source = {(preview.owner_id, preview.source_port_id): preview for preview in previews}
        assert set(by_source) == {(source_id, "image"), (resize_id, "image")}
        assert (
            by_source[(resize_id, "image")].width,
            by_source[(resize_id, "image")].height,
        ) == (32, 24)
        # The client owns the delivery cursors: the call above already advanced
        # them, so a repeat delivery returns nothing.
        assert client.next_image_previews() == ()
    finally:
        client.close()


def test_auto_stop_forgets_the_last_preview_frame(tmp_path: Path) -> None:
    # When a broken graph auto-stops the engine, the preview broker is
    # cleared; a subsequent poll must not re-serve the last preview frame
    # (nor retained note/value previews) as if the engine were still live.
    video = generate_test_video(tmp_path / "auto-stop.mp4", frame_count=DEFAULT_FRAME_COUNT, fps=60)
    registry = create_application_registry()
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    resize_id = document.add_node(
        "synmachine.image.resize",
        parameters={"width": 32, "height": 24, "preserve_aspect": False},
    )
    document.add_connection(source_id, "image", resize_id, "image")
    document.add_connection(
        source_id,
        "image",
        document.add_node("synmachine.visualization.display_image_data", implementation_version=2),
        "image",
    )
    client = InProcessEngineClient(registry)
    try:
        activation = client.activate(document.snapshot())
        assert activation.activated and activation.report.is_valid
        client.play(source_id)
        assert client.wait_until_idle(2.0)
        previews = client.next_image_previews()
        assert previews and all(item.sequence > 0 for item in previews)

        broken = GraphDocument()
        broken.add_node("synmachine.visualization.display_image_data", implementation_version=2)
        activation = client.activate(broken.snapshot())
        assert not activation.activated
        assert not activation.report.is_valid
        assert client.metrics().state is EngineState.STOPPED

        assert client.next_image_previews() == ()
        assert client.next_note_previews() == ()
        assert client.next_value_previews() == ()
    finally:
        client.close()


def test_invalid_source_path_reports_error_without_rejecting_valid_graph(tmp_path: Path) -> None:
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
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


def test_seek_moves_video_playback_position(tmp_path: Path) -> None:
    # 30 frames at 12 fps give a 2.5 s video, so a 0.5 s seek stays inside
    # the played segment and is not clamped.
    video = generate_test_video(tmp_path / "seek.mp4", frame_count=30)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        client.pause(source_id)
        client.seek(source_id, 0.5)
        status = client.source_status(source_id)[0]
        assert status.state is not SourceState.ERROR
        assert status.source_time_s is not None
        assert status.source_time_s == pytest.approx(0.5, abs=0.05)
    finally:
        client.close()


def test_looping_source_honours_the_configured_region(tmp_path: Path) -> None:
    # 24 frames at 12 fps give a 2 s video; the [0.5, 1.5] segment loops once
    # per second, so the source must still be playing long after the whole
    # file (which would end at ~2 s) has passed.
    video = generate_test_video(tmp_path / "loop-region.mp4", frame_count=24, fps=12)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={
            "file_path": str(video),
            "loop": True,
            "loop_start_s": 0.5,
            "loop_end_s": 1.5,
        },
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        client.play(source_id)
        times: list[float] = []
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            status = client.source_status(source_id)[0]
            if status.source_time_s is not None:
                times.append(status.source_time_s)
            if status.state is SourceState.ENDED:
                break
            time.sleep(0.05)
        client.stop(source_id)
        assert times
        assert all(0.5 - 0.05 <= t <= 1.5 + 0.05 for t in times)
        status = client.source_status(source_id)[0]
        assert status.state is SourceState.STOPPED
    finally:
        client.close()


def test_runtime_node_errors_are_exposed_through_typed_engine_metrics(tmp_path: Path) -> None:
    video = generate_test_video(tmp_path / "runtime-error.mp4", frame_count=2)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    resize_id = document.add_node(
        "synmachine.image.resize",
        parameters={"width": 8, "height": 8, "preserve_aspect": False},
    )
    difference_id = document.add_node("synmachine.image.difference")
    # Fresh nodes are stamped at the definition's current implementation version.
    preview_id = document.add_node(
        "synmachine.visualization.display_image_data", implementation_version=2
    )
    document.add_connection(source_id, "image", resize_id, "image")
    document.add_connection(source_id, "image", difference_id, "a")
    document.add_connection(resize_id, "image", difference_id, "b")
    document.add_connection(difference_id, "image", preview_id, "image")
    client = InProcessEngineClient(create_application_registry())
    try:
        activation = client.activate(document.snapshot())
        assert activation.activated and activation.report.is_valid

        client.play(source_id)
        assert client.wait_until_idle(2.0)

        metrics = client.metrics()
        assert metrics.state is EngineState.ERROR
        assert len(metrics.runtime_errors) == 1
        error = metrics.runtime_errors[0]
        assert error.node_id == difference_id
        assert error.code == "difference_shape"
        assert error.recoverable
        assert error.tick_index == 2
    finally:
        client.close()


def test_broken_graph_stops_the_runtime_and_a_valid_graph_restarts_it(
    tmp_path: Path,
) -> None:
    video = generate_test_video(tmp_path / "stop.mp4", frame_count=10, fps=1)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        client.play(source_id)
        assert client.metrics().state is EngineState.RUNNING

        # Breaking the document so that no source can carry signal stops the
        # engine: sources are torn down, the previous plan no longer produces
        # output, and the client stays open. (ADR-0029: a partially valid
        # graph keeps the valid remainder running instead.)
        document.set_parameter(source_id, "loop_start_s", 10.0)
        document.set_parameter(source_id, "loop_end_s", 5.0)
        rejected = client.activate(document.snapshot())

        assert not rejected.activated
        assert not rejected.report.is_valid
        assert client.metrics().state is EngineState.STOPPED
        assert client.source_status() == ()
        assert client.midi_output_status() == ()

        # A valid graph resumes the engine without any manual re-activation.
        restarted = GraphDocument()
        restarted_source_id = restarted.add_node(
            "synmachine.input.load_video",
            implementation_version=2,
            parameters={"file_path": str(video)},
        )
        assert client.activate(restarted.snapshot()).activated
        assert client.source_status(restarted_source_id)[0].state is SourceState.READY
    finally:
        client.close()


def test_playing_sources_auto_resume_after_broken_graph_stop(tmp_path: Path) -> None:
    video = generate_test_video(tmp_path / "resume.mp4", frame_count=10, fps=1)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        client.play(source_id)
        assert client.metrics().state is EngineState.RUNNING

        # A broken stop captures the playing source; a second consecutive
        # broken activation must not wipe the captured memory.
        document.set_parameter(source_id, "loop_start_s", 10.0)
        document.set_parameter(source_id, "loop_end_s", 5.0)
        rejected = client.activate(document.snapshot())
        assert not rejected.activated
        assert client.metrics().state is EngineState.STOPPED
        assert not client.activate(document.snapshot()).activated
        assert client.metrics().state is EngineState.STOPPED

        # Fixing the graph in place resumes the previously playing source.
        document.set_parameter(source_id, "loop_start_s", 0.0)
        document.set_parameter(source_id, "loop_end_s", 0.0)
        assert client.activate(document.snapshot()).activated
        assert client.source_status(source_id)[0].state is SourceState.PLAYING
        assert client.metrics().state is EngineState.RUNNING
    finally:
        client.close()


def test_paused_sources_are_not_auto_played_after_broken_graph_stop(tmp_path: Path) -> None:
    video = generate_test_video(tmp_path / "paused-resume.mp4", frame_count=10, fps=1)
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(create_application_registry())
    try:
        assert client.activate(document.snapshot()).activated
        client.play(source_id)
        assert client.metrics().state is EngineState.RUNNING
        client.pause(source_id)
        assert client.metrics().state is EngineState.PAUSED

        document.set_parameter(source_id, "loop_start_s", 10.0)
        document.set_parameter(source_id, "loop_end_s", 5.0)
        rejected = client.activate(document.snapshot())
        assert not rejected.activated
        assert client.metrics().state is EngineState.STOPPED

        # The user paused this source, so the auto-resume must not play it.
        document.set_parameter(source_id, "loop_start_s", 0.0)
        document.set_parameter(source_id, "loop_end_s", 0.0)
        assert client.activate(document.snapshot()).activated
        assert client.source_status(source_id)[0].state is SourceState.READY
        assert client.metrics().state is EngineState.STOPPED
    finally:
        client.close()


def test_preview_disabled_engine_spins_no_preview_worker(tmp_path: Path) -> None:
    # Offline workloads (MIDI export, UI tests) pay for no preview worker and
    # no broker when previews are disabled; the graph still runs to the end.
    video = generate_test_video(
        tmp_path / "no-previews.mp4", frame_count=DEFAULT_FRAME_COUNT, fps=60
    )
    registry = create_application_registry()
    document = GraphDocument()
    source_id = document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(registry, use_previews=False)
    try:
        activation = client.activate(document.snapshot())
        assert activation.activated and activation.report.is_valid
        client.play(source_id)
        assert client.wait_until_idle(3.0)
        thread_names = {thread.name for thread in threading.enumerate()}
        assert "in-process-preview-worker" not in thread_names
        assert client.next_image_previews() == ()
        assert client.next_note_previews() == ()
        assert client.next_value_previews() == ()
        metrics = client.metrics()
        assert metrics.processed_ticks == DEFAULT_FRAME_COUNT
    finally:
        client.close()


class _PanicRecordingService:
    """Records the facade's panic calls so tests can observe them."""

    def __init__(self) -> None:
        self.panic_count = 0

    def publish(self, frame, configuration) -> None:
        del frame, configuration

    def request_panic(self) -> None:
        return

    def panic(self, timeout_s: float = 2.0) -> None:
        del timeout_s
        self.panic_count += 1

    def refresh_outputs(self) -> None:
        return

    def status(self) -> MidiServiceStatus:
        return MidiServiceStatus(MidiOutputConnectionState.UNSELECTED, "", (), 0, (), 0, None, None)

    def wait_until_idle(self, timeout_s: float = 2.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        return


class _PanicTestVideoSource:
    def __init__(self, node_id: UUID, file_path: str) -> None:
        self.node_id = node_id
        self.file_path = file_path
        self.state = SourceState.READY

    def play(self) -> None:
        self.state = SourceState.PLAYING

    def pause(self) -> None:
        if self.state is SourceState.PLAYING:
            self.state = SourceState.PAUSED

    def resume(self) -> None:
        if self.state is SourceState.PAUSED:
            self.state = SourceState.PLAYING

    def stop(self) -> None:
        self.state = SourceState.STOPPED

    def reload(self) -> None:
        self.state = SourceState.READY

    def seek(self, source_time_s: float) -> None:
        del source_time_s
        raise NotImplementedError

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        return SourceStatus(self.node_id, self.state, self.file_path, dropped_before_processing)

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        self.state = SourceState.CLOSED


class _PanicTestVideoFactory:
    def __init__(self) -> None:
        self.sources: list[_PanicTestVideoSource] = []

    def __call__(
        self,
        node_id: UUID,
        config: VideoSourceConfig,
        on_frame: object,
        on_reset: object,
    ) -> _PanicTestVideoSource:
        del on_frame, on_reset
        source = _PanicTestVideoSource(node_id, config.file_path)
        self.sources.append(source)
        return source


def test_pause_panics_only_when_no_source_stays_playing() -> None:
    service = _PanicRecordingService()
    factory = _PanicTestVideoFactory()
    client = InProcessEngineClient(
        create_application_registry(midi_output_service_factory=lambda: service),
        video_source_factory=factory,
    )
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        node_id=SOURCE_A,
        parameters={"file_path": "a.mp4"},
    )
    document.add_node(
        "synmachine.input.load_video",
        implementation_version=2,
        node_id=SOURCE_B,
        parameters={"file_path": "b.mp4"},
    )
    luminance = document.add_node("synmachine.image.to_luminance")
    document.add_connection(SOURCE_A, "image", luminance, "image")
    pitch = document.add_node("synmachine.synesthesia.channel_to_pitch")
    document.add_connection(luminance, "channel", pitch, "value")
    send_id = document.add_node(
        "synmachine.output.send_midi", parameters={"output_port": "Mock MIDI Out"}
    )
    document.add_connection(pitch, "midi", send_id, "midi")
    try:
        assert client.activate(document.snapshot()).activated
        client.play(SOURCE_A)
        client.play(SOURCE_B)
        assert client.metrics().state is EngineState.RUNNING

        # While source B is still playing, nothing is silenced.
        client.pause(SOURCE_A)
        assert service.panic_count == 0
        assert client.metrics().state is EngineState.RUNNING

        # Pausing the last producing source leaves nothing generating state.
        client.pause(SOURCE_B)
        assert service.panic_count == 1
        assert client.metrics().state is EngineState.PAUSED
    finally:
        client.close()


def test_source_tick_without_declared_outputs_fails_loudly(tmp_path: Path) -> None:
    """A source that publishes frames but declares no output contract on its own
    definition must surface the worker error instead of orphaning its outputs."""

    video_definition = next(
        definition
        for definition in create_input_definitions()
        if definition.execution.type_id == LOAD_VIDEO_TYPE_ID
    )
    undetermined = replace(
        video_definition,
        execution=replace(
            video_definition.execution,
            type_id="test.engine.source_without_contract",
            source_outputs=None,
        ),
    )
    video = generate_test_video(tmp_path / "no-contract.mp4", frame_count=10)
    document = GraphDocument()
    document.add_node(
        "test.engine.source_without_contract",
        implementation_version=video_definition.execution.implementation_version,
        node_id=SOURCE_A,
        parameters={"file_path": str(video)},
    )
    client = InProcessEngineClient(NodeRegistry((undetermined,)))
    try:
        assert client.activate(document.snapshot()).activated
        client.play(SOURCE_A)
        worker = client._body._worker
        assert worker is not None
        deadline = time.monotonic() + 5.0
        while worker.last_error is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker.last_error is not None
        assert "declares no output contract" in worker.last_error
    finally:
        client.close()
