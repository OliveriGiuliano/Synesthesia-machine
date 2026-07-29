"""Windows spawn, versioned IPC, shared memory, and cleanup acceptance tests."""

import time
from multiprocessing.shared_memory import SharedMemory
from uuid import uuid4

import numpy as np
import pytest
from tools.process_ipc_probe import EngineProbeProcess, SharedRgbSlot, generated_rgb_frame

from synesthesia_machine.contracts.engine_messages import (
    Ping,
    Pong,
    SharedFrameReady,
    WriteSharedFrame,
)


def test_child_ping_shared_frame_and_clean_stop() -> None:
    engine = EngineProbeProcess()
    slot = SharedRgbSlot(16, 12)
    shared_memory_name = slot.name
    try:
        engine.start()
        ping_id = uuid4().hex
        pong = engine.request(Ping(request_id=ping_id, sent_monotonic_ns=time.monotonic_ns()))
        assert isinstance(pong, Pong)
        assert pong.request_id == ping_id
        assert pong.child_process_id > 0

        frame_id = uuid4().hex
        ready = engine.request(
            WriteSharedFrame(
                request_id=frame_id,
                shared_memory_name=slot.name,
                width=16,
                height=12,
            )
        )
        assert isinstance(ready, SharedFrameReady)
        assert ready.request_id == frame_id
        np.testing.assert_array_equal(slot.array(), generated_rgb_frame(16, 12))
    finally:
        engine.close()
        engine.close()
        slot.close()
        slot.close()

    with pytest.raises(FileNotFoundError):
        SharedMemory(name=shared_memory_name)


def test_forced_child_termination_and_parent_cleanup() -> None:
    engine = EngineProbeProcess()
    slot = SharedRgbSlot(8, 8)
    shared_memory_name = slot.name
    try:
        engine.start()
        assert engine.process.is_alive()
        engine.force_terminate()
    finally:
        engine.force_terminate()
        slot.close()
        slot.close()

    with pytest.raises(FileNotFoundError):
        SharedMemory(name=shared_memory_name)
