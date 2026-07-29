"""Hardware-independent OpenCV backend fallback tests."""

import threading

import cv2
from tools.camera_probe import CameraBackend, probe_camera_index, submit_camera_probe


class FakeCapture:
    def __init__(self, *, opened: bool) -> None:
        self._opened = opened
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, object]:
        return self._opened, object()

    def get(self, property_id: int, /) -> float:
        values = {
            cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
            cv2.CAP_PROP_FPS: 30.0,
        }
        return values.get(property_id, 0.0)

    def release(self) -> None:
        self.released = True


def test_camera_probe_falls_back_and_releases_handles() -> None:
    captures: list[FakeCapture] = []

    def factory(index: int, backend: int) -> FakeCapture:
        assert index == 2
        capture = FakeCapture(opened=backend == int(CameraBackend.DIRECTSHOW))
        captures.append(capture)
        return capture

    result = probe_camera_index(2, capture_factory=factory)

    assert result.available is True
    assert result.backend == "DIRECTSHOW"
    assert (result.width, result.height, result.fps) == (640, 480, 30.0)
    assert len(captures) == 2
    assert all(capture.released for capture in captures)


def test_camera_probe_runs_on_worker_thread() -> None:
    calling_thread = threading.get_ident()
    probe_threads: list[int] = []

    def factory(index: int, backend: int) -> FakeCapture:
        del index, backend
        probe_threads.append(threading.get_ident())
        return FakeCapture(opened=False)

    executor, future = submit_camera_probe([0], capture_factory=factory)
    try:
        result = future.result(timeout=2)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    assert result[0].available is False
    assert probe_threads
    assert all(thread_id != calling_thread for thread_id in probe_threads)
