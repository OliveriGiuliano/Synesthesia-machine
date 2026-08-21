"""Windows-spawn ping/pong and shared-memory RGB transport proof."""

import argparse
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from multiprocessing.shared_memory import SharedMemory
from typing import Protocol
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts.engine_messages import (
    ENGINE_PROTOCOL_VERSION,
    EngineCommand,
    EngineEvent,
    Ping,
    Pong,
    SharedFrameReady,
    Shutdown,
    ShutdownAcknowledged,
    WriteSharedFrame,
)

DEFAULT_WIDTH = 32
DEFAULT_HEIGHT = 24


class DuplexConnection(Protocol):
    def send(self, obj: object) -> None: ...

    def recv(self) -> object: ...

    def poll(self, timeout: float = 0.0) -> bool: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProbeResult:
    child_process_id: int
    frame_shape: tuple[int, int, int]
    checksum: int
    process_terminated: bool


def generated_rgb_frame(width: int, height: int) -> NDArray[np.uint8]:
    frame = np.empty((height, width, 3), dtype=np.uint8)
    frame[..., 0] = np.arange(width, dtype=np.uint8)[np.newaxis, :]
    frame[..., 1] = np.arange(height, dtype=np.uint8)[:, np.newaxis]
    frame[..., 2] = 127
    return frame


class SharedRgbSlot:
    """Parent-owned shared RGB bytes with idempotent cleanup."""

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            msg = "shared frame dimensions must be positive"
            raise ValueError(msg)
        self.width = width
        self.height = height
        self._shared_memory: SharedMemory | None = SharedMemory(
            create=True, size=width * height * 3
        )

    @property
    def name(self) -> str:
        if self._shared_memory is None:
            msg = "shared RGB slot is closed"
            raise RuntimeError(msg)
        return self._shared_memory.name

    def array(self) -> NDArray[np.uint8]:
        if self._shared_memory is None:
            msg = "shared RGB slot is closed"
            raise RuntimeError(msg)
        return np.ndarray(
            (self.height, self.width, 3), dtype=np.uint8, buffer=self._shared_memory.buf
        )

    def close(self) -> None:
        shared_memory = self._shared_memory
        if shared_memory is None:
            return
        self._shared_memory = None
        shared_memory.close()
        with suppress(FileNotFoundError):
            shared_memory.unlink()

    def __enter__(self) -> "SharedRgbSlot":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        self.close()


def _validate_protocol(protocol_version: int) -> None:
    if protocol_version != ENGINE_PROTOCOL_VERSION:
        msg = f"Unsupported engine protocol {protocol_version}"
        raise ValueError(msg)


def engine_child(connection: DuplexConnection) -> None:
    """Top-level child target required by the Windows spawn start method."""

    try:
        while True:
            command = connection.recv()
            if not isinstance(command, (Ping, WriteSharedFrame, Shutdown)):
                msg = f"Unsupported engine command: {type(command).__name__}"
                raise TypeError(msg)
            _validate_protocol(command.protocol_version)

            event: EngineEvent
            if isinstance(command, Ping):
                event = Pong(request_id=command.request_id, child_process_id=os.getpid())
            elif isinstance(command, WriteSharedFrame):
                shared_memory = SharedMemory(name=command.shared_memory_name)
                try:
                    target = np.ndarray(
                        (command.height, command.width, 3),
                        dtype=np.uint8,
                        buffer=shared_memory.buf,
                    )
                    np.copyto(target, generated_rgb_frame(command.width, command.height))
                finally:
                    shared_memory.close()
                event = SharedFrameReady(request_id=command.request_id, sequence=1)
            else:
                event = ShutdownAcknowledged(request_id=command.request_id)
                connection.send(event)
                break
            connection.send(event)
    finally:
        connection.close()


class EngineProbeProcess:
    """Small parent-side lifecycle wrapper used by the IPC smoke tests."""

    def __init__(self) -> None:
        self._context: SpawnContext = get_context("spawn")
        self._connection: DuplexConnection | None = None
        self._process: BaseProcess | None = None

    @property
    def process(self) -> BaseProcess:
        if self._process is None:
            msg = "engine probe process has not started"
            raise RuntimeError(msg)
        return self._process

    def start(self) -> None:
        if self._process is not None:
            msg = "engine probe process already started"
            raise RuntimeError(msg)
        parent_connection, child_connection = self._context.Pipe(duplex=True)
        process = self._context.Process(target=engine_child, args=(child_connection,))
        try:
            process.start()
            self._connection = parent_connection
            self._process = process
        except Exception:
            parent_connection.close()
            process.close()
            raise
        finally:
            child_connection.close()

    def request(self, command: EngineCommand, *, timeout_seconds: float = 5.0) -> EngineEvent:
        connection = self._connection
        if connection is None:
            msg = "engine probe process is not running"
            raise RuntimeError(msg)
        connection.send(command)
        if not connection.poll(timeout_seconds):
            msg = f"Timed out waiting for {type(command).__name__} response"
            raise TimeoutError(msg)
        event = connection.recv()
        if not isinstance(event, (Pong, SharedFrameReady, ShutdownAcknowledged)):
            msg = f"Unsupported engine event: {type(event).__name__}"
            raise TypeError(msg)
        return event

    def close(self, *, timeout_seconds: float = 5.0) -> None:
        process = self._process
        connection = self._connection
        self._process = None
        self._connection = None
        if process is None:
            return
        try:
            if process.is_alive() and connection is not None:
                with suppress(BrokenPipeError, EOFError, OSError):
                    request_id = uuid4().hex
                    connection.send(Shutdown(request_id=request_id))
                    if connection.poll(timeout_seconds):
                        connection.recv()
            self._stop_process(process, timeout_seconds=timeout_seconds)
        finally:
            if connection is not None:
                with suppress(OSError):
                    connection.close()
            if not process.is_alive():
                process.close()

    def force_terminate(self, *, timeout_seconds: float = 5.0) -> None:
        process = self._process
        connection = self._connection
        self._process = None
        self._connection = None
        if process is None:
            return
        try:
            self._stop_process(process, timeout_seconds=timeout_seconds, terminate_first=True)
        finally:
            if connection is not None:
                with suppress(OSError):
                    connection.close()
            if not process.is_alive():
                process.close()

    @staticmethod
    def _stop_process(
        process: BaseProcess,
        *,
        timeout_seconds: float,
        terminate_first: bool = False,
    ) -> None:
        if terminate_first and process.is_alive():
            process.terminate()
        process.join(timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(timeout_seconds)
        if process.is_alive():
            process.kill()
            process.join(timeout_seconds)
        if process.is_alive():
            msg = f"Engine probe process {process.pid} did not terminate"
            raise TimeoutError(msg)


def run_probe(width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> ProbeResult:
    engine = EngineProbeProcess()
    try:
        with SharedRgbSlot(width, height) as slot:
            engine.start()
            ping_id = uuid4().hex
            pong = engine.request(Ping(request_id=ping_id, sent_monotonic_ns=time.monotonic_ns()))
            if not isinstance(pong, Pong) or pong.request_id != ping_id:
                msg = "engine returned an invalid pong"
                raise RuntimeError(msg)

            frame_id = uuid4().hex
            ready = engine.request(
                WriteSharedFrame(
                    request_id=frame_id,
                    shared_memory_name=slot.name,
                    width=width,
                    height=height,
                )
            )
            if not isinstance(ready, SharedFrameReady) or ready.request_id != frame_id:
                msg = "engine returned an invalid shared-frame acknowledgement"
                raise RuntimeError(msg)

            frame = slot.array().copy()
            expected = generated_rgb_frame(width, height)
            if not np.array_equal(frame, expected):
                msg = "shared frame contents differ from generated frame"
                raise RuntimeError(msg)
            return ProbeResult(
                child_process_id=pong.child_process_id,
                frame_shape=(height, width, 3),
                checksum=int(frame.sum()),
                process_terminated=True,
            )
    finally:
        engine.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = run_probe()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
