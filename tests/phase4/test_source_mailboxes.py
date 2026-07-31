"""Bounded latest-frame mailbox metrics and slow-graph acceptance tests."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

import numpy as np

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    ParameterValue,
    PortType,
    RuntimeValue,
    SourceState,
    SourceStatus,
    read_only_float32,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.media import PresentedVideoFrame
from synesthesia_machine.nodes import (
    CachePolicy,
    ExecutionKind,
    InputPortSpec,
    NodeDefinition,
    ResetReason,
)
from synesthesia_machine.nodes.input import create_input_definitions
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime.in_process_engine import InProcessEngineClient

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000005000")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000005001")
SLOW_SINK_ID = UUID("00000000-0000-0000-0000-000000005002")
_NANOSECONDS_PER_MILLISECOND = 1_000_000


class _LogicalClock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._now_ns = 0

    def __call__(self) -> int:
        with self._lock:
            return self._now_ns

    def set_ms(self, value: int) -> None:
        with self._lock:
            self._now_ns = value * _NANOSECONDS_PER_MILLISECOND


class _BlockingSinkRuntime:
    def __init__(self) -> None:
        self.started = (threading.Event(), threading.Event())
        self.release = (threading.Event(), threading.Event())
        self.tick_indices: list[int] = []

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters
        invocation = len(self.tick_indices)
        self.tick_indices.append(context.tick_index)
        if invocation < len(self.started):
            self.started[invocation].set()
            if not self.release[invocation].wait(2.0):
                raise TimeoutError("test did not release deliberately slow sink")
        return {}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        for event in self.release:
            event.set()


class _BlockingSinkFactory:
    def __init__(self) -> None:
        self.runtime: _BlockingSinkRuntime | None = None

    def __call__(self, node_id: UUID) -> _BlockingSinkRuntime:
        assert node_id == SLOW_SINK_ID
        self.runtime = _BlockingSinkRuntime()
        return self.runtime


class _ManualVideoSource:
    def __init__(
        self,
        node_id: UUID,
        file_path: str | Path,
        publish: Callable[[PresentedVideoFrame], None],
    ) -> None:
        self.node_id = node_id
        self._file_path = str(file_path)
        self._publish = publish
        self._state = SourceState.READY
        self._processed_index = 0

    def emit(self, packet: PresentedVideoFrame) -> None:
        self._processed_index = packet.processed_index
        self._publish(packet)

    def play(self) -> None:
        self._state = SourceState.PLAYING

    def pause(self) -> None:
        self._state = SourceState.PAUSED

    def resume(self) -> None:
        self._state = SourceState.PLAYING

    def stop(self) -> None:
        self._state = SourceState.STOPPED

    def reload(self) -> None:
        self._state = SourceState.READY

    def seek(self, source_time_s: float) -> None:
        del source_time_s
        raise NotImplementedError

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        return SourceStatus(
            self.node_id,
            self._state,
            self._file_path,
            processed_index=self._processed_index,
            dropped_before_processing=dropped_before_processing,
        )

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        self._state = SourceState.CLOSED


class _ManualVideoFactory:
    def __init__(self) -> None:
        self.source: _ManualVideoSource | None = None

    def __call__(
        self,
        node_id: UUID,
        file_path: str | Path,
        *,
        process_every_nth_frame: int,
        loop: bool,
        stream_index: int,
        on_frame: object,
        on_reset: object,
    ) -> _ManualVideoSource:
        del process_every_nth_frame, loop, stream_index, on_reset
        assert callable(on_frame)
        source = _ManualVideoSource(
            node_id,
            file_path,
            cast(Callable[[PresentedVideoFrame], None], on_frame),
        )
        self.source = source
        return source


def _slow_sink_definition(factory: _BlockingSinkFactory) -> NodeDefinition:
    return NodeDefinition(
        "test.phase4.slow_image_sink",
        1,
        "Slow image sink",
        "Test",
        "Deliberately blocks graph execution to exercise source backpressure.",
        (InputPortSpec("image", "Image", PortType.IMAGE),),
        (),
        (),
        ExecutionKind.SINK,
        factory,
        cache_policy=CachePolicy.NEVER,
    )


def _packet(tick_index: int, received_ms: int) -> PresentedVideoFrame:
    context = FrameContext(
        SOURCE_ID,
        tick_index,
        tick_index - 1,
        (tick_index - 1) / 60.0,
        received_ms * _NANOSECONDS_PER_MILLISECOND,
        None,
        True,
    )
    image = ImageFrame(
        read_only_float32(np.zeros((1, 1, 3), dtype=np.float32)),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(SOURCE_ID, "simulated"),
    )
    return PresentedVideoFrame(image, tick_index)


def test_slow_graph_drops_stale_frames_with_bounded_latency_and_visible_metrics() -> None:
    clock = _LogicalClock()
    sink_factory = _BlockingSinkFactory()
    source_factory = _ManualVideoFactory()
    registry = NodeRegistry((create_input_definitions()[0], _slow_sink_definition(sink_factory)))
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node(
        "synmachine.input.load_video",
        node_id=SOURCE_ID,
        parameters={"file_path": "simulated.mp4"},
    )
    document.add_node("test.phase4.slow_image_sink", node_id=SLOW_SINK_ID)
    document.add_connection(SOURCE_ID, "image", SLOW_SINK_ID, "image")
    client = InProcessEngineClient(
        registry,
        video_source_factory=source_factory,
        worker_clock=clock,
    )
    try:
        assert client.activate(document.snapshot()).activated
        source = source_factory.source
        runtime = sink_factory.runtime
        assert source is not None and runtime is not None
        client.play(SOURCE_ID)

        source.emit(_packet(1, 0))
        assert runtime.started[0].wait(1.0)
        for tick_index in range(2, 11):
            received_ms = tick_index * 10 - 10
            clock.set_ms(received_ms)
            source.emit(_packet(tick_index, received_ms))

        busy_status = client.source_status(SOURCE_ID)[0]
        busy_metrics = client.metrics()
        assert busy_status.mailbox_occupancy == 2
        assert busy_status.mailbox_capacity == 2
        assert busy_status.dropped_before_processing == 8
        assert busy_metrics.mailbox_occupancy == 2
        assert busy_metrics.mailbox_capacity == 2
        assert busy_metrics.dropped_before_processing == 8

        clock.set_ms(100)
        runtime.release[0].set()
        assert runtime.started[1].wait(1.0)
        assert runtime.tick_indices == [1, 10]
        processing_status = client.source_status(SOURCE_ID)[0]
        assert processing_status.mailbox_occupancy == 1

        clock.set_ms(120)
        runtime.release[1].set()
        assert client.wait_until_idle(1.0)

        status = client.source_status(SOURCE_ID)[0]
        metrics = client.metrics()
        assert runtime.tick_indices == [1, 10]
        assert status.mailbox_occupancy == 0
        assert status.mailbox_capacity == 2
        assert status.dropped_before_processing == 8
        assert status.processing_latency_ms == 30.0
        assert status.frame_age_ms == 30.0
        assert metrics.processed_ticks == 2
        assert metrics.mailbox_occupancy == 0
        assert metrics.mailbox_capacity == 2
        assert metrics.processing_latency_ms == 30.0
        assert metrics.frame_age_ms == 30.0
    finally:
        if sink_factory.runtime is not None:
            for event in sink_factory.runtime.release:
                event.set()
        client.close()
