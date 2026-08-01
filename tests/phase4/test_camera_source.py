"""Simulated Load Camera backend, capture, reconnect, and engine wiring tests."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

import cv2
import numpy as np
import pytest

from synesthesia_machine.contracts import (
    FrameContext,
    ImageFrame,
    NoData,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.media import (
    CameraBackend,
    CameraBackendPreference,
    CameraDevice,
    CameraEnumerationService,
    CameraSourceService,
    CameraUnavailableError,
    PresentedSourceFrame,
    camera_index_from_device_id,
    open_camera,
)
from synesthesia_machine.nodes import ExecutionKind, ParameterUpdateMode, ResetReason
from synesthesia_machine.nodes.input import LOAD_CAMERA_TYPE_ID, create_input_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import ProcessEngineClient
from synesthesia_machine.runtime.in_process_engine import InProcessEngineClient

SOURCE_ID = UUID("00000000-0000-0000-0000-000000006001")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000006002")


class _FakeCapture:
    def __init__(
        self,
        *,
        opened: bool = True,
        reads: Iterable[tuple[bool, object]] = (),
        properties: dict[int, float] | None = None,
        block_when_empty: bool = False,
    ) -> None:
        self.opened = opened
        self.reads = deque(reads)
        self.properties = dict(properties or {})
        self.block_when_empty = block_when_empty
        self.set_calls: list[tuple[int, float]] = []
        self.release_count = 0
        self._released = threading.Event()

    def isOpened(self) -> bool:
        return self.opened

    def read(self) -> tuple[bool, object]:
        if self.reads:
            return self.reads.popleft()
        if self.block_when_empty:
            self._released.wait(2.0)
        return False, None

    def get(self, property_id: int, /) -> float:
        return self.properties.get(property_id, 0.0)

    def set(self, property_id: int, value: float, /) -> bool:
        self.set_calls.append((property_id, value))
        return True

    def release(self) -> None:
        self.release_count += 1
        self._released.set()


@dataclass(slots=True)
class _CaptureFactory:
    captures: deque[_FakeCapture]
    calls: list[tuple[int, int]] = field(default_factory=lambda: list[tuple[int, int]]())
    thread_ids: list[int] = field(default_factory=lambda: list[int]())

    @classmethod
    def from_captures(cls, *captures: _FakeCapture) -> _CaptureFactory:
        return cls(deque(captures))

    def __call__(self, index: int, backend: int) -> _FakeCapture:
        self.calls.append((index, backend))
        self.thread_ids.append(threading.get_ident())
        if not self.captures:
            raise AssertionError("simulated camera factory received an unexpected open request")
        return self.captures.popleft()


class _SequenceClock:
    def __init__(self, *values_ns: int) -> None:
        self._lock = threading.Lock()
        self._values = deque(values_ns)
        self._last_ns = values_ns[-1] if values_ns else 0
        self.waits: list[float | None] = []

    def monotonic_ns(self) -> int:
        with self._lock:
            if self._values:
                self._last_ns = self._values.popleft()
            else:
                self._last_ns += 1_000_000
            return self._last_ns

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool:
        self.waits.append(timeout_s)
        if timeout_s is None:
            return wake_event.wait(1.0)
        return wake_event.is_set()


def _camera_definition():
    return next(
        definition
        for definition in create_input_definitions()
        if definition.type_id == LOAD_CAMERA_TYPE_ID
    )


def _bgr(blue: int, green: int, red: int) -> np.ndarray:
    return np.array([[[blue, green, red]]], dtype=np.uint8)


def test_load_camera_definition_has_stable_restart_source_contract() -> None:
    definition = _camera_definition()

    assert definition.display_name == "Load Camera"
    assert definition.execution_kind is ExecutionKind.SOURCE
    assert tuple(port.id for port in definition.outputs) == ("image", "processed_index")
    assert tuple(parameter.id for parameter in definition.parameters) == (
        "device_id",
        "requested_width",
        "requested_height",
        "requested_fps",
        "backend_preference",
        "process_every_nth_frame",
        "reconnect_automatically",
    )
    assert all(
        parameter.update_mode is ParameterUpdateMode.RESTART_SOURCE
        for parameter in definition.parameters
    )
    assert definition.parameter("device_id").default == "opencv:0"  # type: ignore[union-attr]
    assert definition.parameter("requested_width").default == 1280  # type: ignore[union-attr]
    assert definition.parameter("requested_height").default == 720  # type: ignore[union-attr]
    assert definition.parameter("requested_fps").default == 30.0  # type: ignore[union-attr]
    assert definition.parameter("backend_preference").choices == (  # type: ignore[union-attr]
        "AUTO",
        "MEDIA_FOUNDATION",
        "DIRECTSHOW",
    )
    assert definition.parameter("process_every_nth_frame").minimum == 1  # type: ignore[union-attr]
    assert definition.aliases == ("camera", "webcam", "live camera")


@pytest.mark.parametrize(
    ("device_id", "expected"),
    (("opencv:0", 0), ("opencv:1", 1), ("opencv:42", 42)),
)
def test_camera_device_ids_are_parsed_exactly(device_id: str, expected: int) -> None:
    assert camera_index_from_device_id(device_id) == expected


@pytest.mark.parametrize(
    "device_id",
    ("", "0", "opencv:-1", "opencv:01", " opencv:0", "opencv:0 ", "camera:0"),
)
def test_camera_device_ids_reject_non_enumerated_syntax(device_id: str) -> None:
    with pytest.raises(ValueError, match="select an enumerated ID"):
        camera_index_from_device_id(device_id)


def test_open_camera_falls_back_to_dshow_and_reports_negotiated_properties() -> None:
    failed_msmf = _FakeCapture(opened=False)
    dshow = _FakeCapture(
        properties={
            cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
            cv2.CAP_PROP_FPS: 29.97,
        }
    )
    factory = _CaptureFactory.from_captures(failed_msmf, dshow)

    opened = open_camera(
        2,
        requested_width=1280,
        requested_height=720,
        requested_fps=60.0,
        capture_factory=factory,
    )
    try:
        assert factory.calls == [
            (2, int(CameraBackend.MEDIA_FOUNDATION)),
            (2, int(CameraBackend.DIRECTSHOW)),
        ]
        assert failed_msmf.release_count == 1
        assert opened.capture is dshow
        assert opened.backend is CameraBackend.DIRECTSHOW
        assert (opened.width, opened.height, opened.fps) == (640, 480, 29.97)
        assert dshow.set_calls == [
            (cv2.CAP_PROP_BUFFERSIZE, 1.0),
            (cv2.CAP_PROP_FRAME_WIDTH, 1280.0),
            (cv2.CAP_PROP_FRAME_HEIGHT, 720.0),
            (cv2.CAP_PROP_FPS, 60.0),
        ]
    finally:
        opened.capture.release()


def test_explicit_camera_backend_never_substitutes_another_backend() -> None:
    unavailable = _FakeCapture(opened=False)
    factory = _CaptureFactory.from_captures(unavailable)

    with pytest.raises(CameraUnavailableError, match="DIRECTSHOW: unavailable"):
        open_camera(
            3,
            backend_preference=CameraBackendPreference.DIRECTSHOW,
            capture_factory=factory,
        )

    assert factory.calls == [(3, int(CameraBackend.DIRECTSHOW))]
    assert unavailable.release_count == 1


def test_camera_enumeration_is_deduplicated_off_thread_and_cached() -> None:
    main_thread_id = threading.get_ident()
    started = threading.Event()
    allow_probe = threading.Event()
    captures: list[_FakeCapture] = []
    factory_calls: list[tuple[int, int]] = []
    factory_threads: list[int] = []

    def factory(index: int, backend: int) -> _FakeCapture:
        factory_calls.append((index, backend))
        factory_threads.append(threading.get_ident())
        started.set()
        assert allow_probe.wait(1.0)
        capture = _FakeCapture(
            properties={
                cv2.CAP_PROP_FRAME_WIDTH: 1920.0,
                cv2.CAP_PROP_FRAME_HEIGHT: 1080.0,
                cv2.CAP_PROP_FPS: 30.0,
            }
        )
        captures.append(capture)
        return capture

    service = CameraEnumerationService(
        indexes=(4,),
        capture_factory=factory,
        cache_duration_s=60.0,
        monotonic_ns=lambda: 100,
    )
    try:
        first = service.enumerate_async()
        assert started.wait(1.0)
        duplicate = service.enumerate_async()
        assert duplicate is first
        allow_probe.set()

        expected = (CameraDevice("opencv:4", "Camera 4", 4, "MEDIA_FOUNDATION", 1920, 1080, 30.0),)
        assert first.result(timeout=1.0) == expected
        assert service.cached() == expected
        cached = service.enumerate_async()
        assert cached is not first
        assert cached.done() and cached.result() == expected
        assert factory_calls == [(4, int(CameraBackend.MEDIA_FOUNDATION))]
        assert factory_threads and factory_threads[0] != main_thread_id
        assert captures[0].release_count == 1
    finally:
        allow_probe.set()
        service.close()


def test_camera_capture_converts_bgr_selects_frames_and_reports_status() -> None:
    first = _bgr(0, 127, 255)
    skipped = _bgr(10, 20, 30)
    third = _bgr(255, 0, 64)
    capture = _FakeCapture(
        reads=((True, first), (True, skipped), (True, third)),
        properties={
            cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 360.0,
            cv2.CAP_PROP_FPS: 24.0,
        },
        block_when_empty=True,
    )
    factory = _CaptureFactory.from_captures(capture)
    clock = _SequenceClock(1_000_000_000, 1_010_000_000, 1_020_000_000, 1_030_000_000)
    received: list[PresentedSourceFrame] = []
    enough_frames = threading.Event()

    def on_frame(packet: PresentedSourceFrame) -> None:
        received.append(packet)
        if len(received) == 2:
            enough_frames.set()

    source = CameraSourceService(
        SOURCE_ID,
        "opencv:0",
        requested_width=640,
        requested_height=360,
        requested_fps=24.0,
        backend_preference=CameraBackendPreference.DIRECTSHOW,
        process_every_nth_frame=2,
        on_frame=on_frame,
        capture_factory=factory,
        clock=clock,
    )
    try:
        assert source.status().state is SourceState.READY
        source.play()
        assert enough_frames.wait(1.0)

        images: list[ImageFrame] = []
        for packet in received:
            assert isinstance(packet.image, ImageFrame)
            images.append(packet.image)
        assert [packet.processed_index for packet in received] == [1, 2]
        assert [image.context.source_frame_index for image in images] == [0, 2]
        assert [image.context.received_monotonic_ns for image in images] == [
            1_010_000_000,
            1_030_000_000,
        ]
        assert [image.context.source_time_s for image in images] == [0.01, 0.03]
        assert np.allclose(images[0].data[0, 0], (1.0, 127.0 / 255.0, 0.0))
        assert np.allclose(images[1].data[0, 0], (64.0 / 255.0, 0.0, 1.0))
        assert all(image.data.dtype == np.float32 for image in images)
        assert all(image.data.flags.c_contiguous for image in images)
        assert all(not image.data.flags.writeable for image in images)
        assert all(image.provenance.source_kind == "camera" for image in images)

        status = source.status()
        assert status.state is SourceState.PLAYING
        assert status.source_kind == "camera"
        assert status.device_id == "opencv:0"
        assert status.backend == "DIRECTSHOW"
        assert (status.width, status.height, status.negotiated_fps) == (640, 360, 24.0)
        assert (status.requested_width, status.requested_height, status.requested_fps) == (
            640,
            360,
            24.0,
        )
        assert status.source_frame_index == 2
        assert status.processed_index == 2
        assert status.skipped_by_selection == 1
    finally:
        source.close()


def test_disconnect_publishes_no_data_resets_and_resumes_after_reconnect() -> None:
    first_capture = _FakeCapture(reads=((True, _bgr(0, 0, 255)), (False, None)))
    replacement_capture = _FakeCapture(
        reads=((True, _bgr(0, 255, 0)),),
        block_when_empty=True,
    )
    factory = _CaptureFactory.from_captures(first_capture, replacement_capture)
    clock = _SequenceClock(0, 10_000_000, 20_000_000, 30_000_000, 40_000_000)
    received: list[PresentedSourceFrame] = []
    resets: list[ResetReason] = []
    reconnected = threading.Event()

    def on_frame(packet: PresentedSourceFrame) -> None:
        received.append(packet)
        if len(received) == 3:
            reconnected.set()

    source = CameraSourceService(
        SOURCE_ID,
        "opencv:1",
        backend_preference=CameraBackendPreference.DIRECTSHOW,
        on_frame=on_frame,
        on_reset=resets.append,
        capture_factory=factory,
        clock=clock,
        reconnect_initial_s=0.25,
        reconnect_max_s=1.0,
    )
    try:
        source.play()
        assert reconnected.wait(1.0)

        assert [packet.processed_index for packet in received] == [1, 2, 3]
        assert isinstance(received[0].image, ImageFrame)
        assert received[1].image is NoData
        assert received[1].context.source_frame_index is None
        assert isinstance(received[2].image, ImageFrame)
        assert resets == [ResetReason.SOURCE_RESTARTED]
        assert clock.waits == [0.25]
        assert factory.calls == [
            (1, int(CameraBackend.DIRECTSHOW)),
            (1, int(CameraBackend.DIRECTSHOW)),
        ]
        assert first_capture.release_count == 1

        status = source.status()
        assert status.state is SourceState.PLAYING
        assert status.reconnect_attempts == 1
        assert status.warnings == 1
        assert status.last_error is None
    finally:
        source.close()


def test_initial_open_failures_back_off_with_cap_then_reset_before_first_frame() -> None:
    failed = tuple(_FakeCapture(opened=False) for _ in range(3))
    connected = _FakeCapture(
        reads=((True, _bgr(1, 2, 3)),),
        block_when_empty=True,
    )
    factory = _CaptureFactory.from_captures(*failed, connected)
    clock = _SequenceClock(0, 1_000_000, 2_000_000, 3_000_000, 4_000_000)
    events: list[str] = []
    first_image = threading.Event()

    def on_frame(packet: PresentedSourceFrame) -> None:
        if packet.image is NoData:
            events.append("outage")
        else:
            events.append("image")
            first_image.set()

    source = CameraSourceService(
        SOURCE_ID,
        "opencv:5",
        backend_preference=CameraBackendPreference.DIRECTSHOW,
        on_frame=on_frame,
        on_reset=lambda reason: events.append(f"reset:{reason.value}"),
        capture_factory=factory,
        clock=clock,
        reconnect_initial_s=0.25,
        reconnect_max_s=0.5,
    )
    try:
        source.play()
        assert first_image.wait(1.0)

        assert events == [
            "outage",
            "outage",
            "outage",
            "reset:SOURCE_RESTARTED",
            "image",
        ]
        assert clock.waits == [0.25, 0.5, 0.5]
        assert factory.calls == [
            (5, int(CameraBackend.DIRECTSHOW)),
            (5, int(CameraBackend.DIRECTSHOW)),
            (5, int(CameraBackend.DIRECTSHOW)),
            (5, int(CameraBackend.DIRECTSHOW)),
        ]
        assert all(capture.release_count == 1 for capture in failed)
        status = source.status()
        assert status.state is SourceState.PLAYING
        assert status.reconnect_attempts == 3
        assert status.warnings == 3
        assert status.last_error is None
    finally:
        source.close()


def test_disconnect_without_auto_reconnect_becomes_unavailable() -> None:
    capture = _FakeCapture(reads=((False, None),))
    factory = _CaptureFactory.from_captures(capture)
    received: list[PresentedSourceFrame] = []
    outage = threading.Event()

    def on_frame(packet: PresentedSourceFrame) -> None:
        received.append(packet)
        outage.set()

    source = CameraSourceService(
        SOURCE_ID,
        "opencv:0",
        backend_preference=CameraBackendPreference.DIRECTSHOW,
        reconnect_automatically=False,
        on_frame=on_frame,
        capture_factory=factory,
        clock=_SequenceClock(0, 1_000_000, 2_000_000),
    )
    try:
        source.play()
        assert outage.wait(1.0)
        assert len(received) == 1 and received[0].image is NoData
        status = source.status()
        assert status.state is SourceState.UNAVAILABLE
        assert status.reconnect_attempts == 1
        assert status.last_error == "Camera disconnected while capturing"
        assert factory.calls == [(0, int(CameraBackend.DIRECTSHOW))]
    finally:
        source.close()


def test_stop_interrupts_camera_reconnect_backoff() -> None:
    unavailable = _FakeCapture(opened=False)
    factory = _CaptureFactory.from_captures(unavailable)
    outage = threading.Event()
    source = CameraSourceService(
        SOURCE_ID,
        "opencv:0",
        backend_preference=CameraBackendPreference.DIRECTSHOW,
        on_frame=lambda packet: outage.set(),
        capture_factory=factory,
        reconnect_initial_s=5.0,
        reconnect_max_s=5.0,
    )
    try:
        source.play()
        assert outage.wait(1.0)
        started = time.monotonic()
        source.stop()
        elapsed_s = time.monotonic() - started

        assert elapsed_s < 0.5
        assert source.status().state is SourceState.STOPPED
        assert factory.calls == [(0, int(CameraBackend.DIRECTSHOW))]
    finally:
        source.close()


class _ManualCameraSource:
    def __init__(
        self,
        node_id: UUID,
        device_id: str,
        publish: Callable[[PresentedSourceFrame], None],
        reset: Callable[[ResetReason], None],
    ) -> None:
        self.node_id = node_id
        self.device_id = device_id
        self.publish = publish
        self.reset = reset
        self.state = SourceState.READY
        self.events: list[str] = []
        self.close_count = 0

    def emit(self, packet: PresentedSourceFrame) -> None:
        self.publish(packet)

    def play(self) -> None:
        self.events.append("play")
        self.state = SourceState.PLAYING

    def pause(self) -> None:
        self.events.append("pause")
        if self.state in {SourceState.PLAYING, SourceState.RECONNECTING}:
            self.state = SourceState.PAUSED

    def resume(self) -> None:
        self.events.append("resume")
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
        return SourceStatus(
            self.node_id,
            self.state,
            source_kind="camera",
            device_id=self.device_id,
            dropped_before_processing=dropped_before_processing,
        )

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        self.events.append("close")
        self.close_count += 1
        self.state = SourceState.CLOSED


@dataclass(slots=True)
class _ManualCameraFactory:
    sources: list[_ManualCameraSource] = field(default_factory=lambda: list[_ManualCameraSource]())
    calls: list[dict[str, object]] = field(default_factory=lambda: list[dict[str, object]]())

    def __call__(
        self,
        node_id: UUID,
        device_id: str,
        *,
        requested_width: int,
        requested_height: int,
        requested_fps: float,
        backend_preference: CameraBackendPreference,
        process_every_nth_frame: int,
        reconnect_automatically: bool,
        on_frame: object,
        on_reset: object,
    ) -> _ManualCameraSource:
        self.calls.append(
            {
                "node_id": node_id,
                "device_id": device_id,
                "requested_width": requested_width,
                "requested_height": requested_height,
                "requested_fps": requested_fps,
                "backend_preference": backend_preference,
                "process_every_nth_frame": process_every_nth_frame,
                "reconnect_automatically": reconnect_automatically,
            }
        )
        source = _ManualCameraSource(
            node_id,
            device_id,
            cast(Callable[[PresentedSourceFrame], None], on_frame),
            cast(Callable[[ResetReason], None], on_reset),
        )
        self.sources.append(source)
        return source


def _camera_document(*, requested_fps: float = 50.0) -> GraphDocument:
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node(
        LOAD_CAMERA_TYPE_ID,
        node_id=SOURCE_ID,
        parameters={
            "device_id": "opencv:7",
            "requested_width": 800,
            "requested_height": 600,
            "requested_fps": requested_fps,
            "backend_preference": "DIRECTSHOW",
            "process_every_nth_frame": 3,
            "reconnect_automatically": False,
        },
    )
    return document


def _outage_packet(tick_index: int) -> PresentedSourceFrame:
    context = FrameContext(SOURCE_ID, tick_index, None, 0.0, tick_index, None, True)
    return PresentedSourceFrame(NoData, tick_index, context)


def test_engine_wires_camera_parameters_packets_and_atomic_source_lifecycle() -> None:
    factory = _ManualCameraFactory()
    client = InProcessEngineClient(
        NodeRegistry(create_input_definitions()),
        camera_source_factory=factory,
    )
    document = _camera_document()
    try:
        assert client.activate(document.snapshot()).activated
        assert factory.calls == [
            {
                "node_id": SOURCE_ID,
                "device_id": "opencv:7",
                "requested_width": 800,
                "requested_height": 600,
                "requested_fps": 50.0,
                "backend_preference": CameraBackendPreference.DIRECTSHOW,
                "process_every_nth_frame": 3,
                "reconnect_automatically": False,
            }
        ]
        original = factory.sources[0]
        assert client.source_status(SOURCE_ID)[0].state is SourceState.READY

        client.play(SOURCE_ID)
        original.emit(_outage_packet(1))
        assert client.wait_until_idle(1.0)
        assert client.metrics().processed_ticks == 1
        assert client.metrics().state.value == "RUNNING"

        original.state = SourceState.RECONNECTING
        document.set_position(SOURCE_ID, (10.0, 20.0))
        assert client.activate(document.snapshot()).activated
        assert factory.sources == [original]
        assert original.events[-2:] == ["pause", "resume"]

        document.set_parameter(SOURCE_ID, "requested_fps", 25.0)
        assert client.activate(document.snapshot()).activated
        replacement = factory.sources[1]
        assert original.close_count == 1
        assert replacement.state is SourceState.PLAYING
        assert replacement.events == ["play"]
        assert factory.calls[1]["requested_fps"] == 25.0
    finally:
        client.close()


def test_process_activation_registers_camera_without_opening_hardware() -> None:
    client = ProcessEngineClient(close_timeout_s=0.5)
    document = _camera_document(requested_fps=25.0)
    try:
        activation = client.activate(document.snapshot())
        engine_status = client.status()
        statuses = client.source_status(SOURCE_ID)

        assert activation.activated
        assert engine_status.child_process_id is not None
        assert engine_status.child_process_id != os.getpid()
        assert len(statuses) == 1
        status = statuses[0]
        assert status.state is SourceState.READY
        assert status.source_kind == "camera"
        assert status.device_id == "opencv:7"
        assert (status.requested_width, status.requested_height, status.requested_fps) == (
            800,
            600,
            25.0,
        )
        assert status.backend is None
        assert status.source_frame_index is None
        assert status.processed_index == 0
    finally:
        client.close()
