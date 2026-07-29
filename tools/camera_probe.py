"""Probe OpenCV camera indexes off the UI thread using Windows backend fallback."""

import argparse
import json
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Protocol

import cv2


class CaptureHandle(Protocol):
    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, object]: ...

    def get(self, property_id: int, /) -> float: ...

    def release(self) -> None: ...


class CameraBackend(IntEnum):
    MEDIA_FOUNDATION = cv2.CAP_MSMF
    DIRECTSHOW = cv2.CAP_DSHOW


@dataclass(frozen=True, slots=True)
class CameraProbeResult:
    index: int
    available: bool
    backend: str | None
    width: int | None
    height: int | None
    fps: float | None
    error: str | None = None


CaptureFactory = Callable[[int, int], CaptureHandle]


def _default_capture_factory(index: int, backend: int) -> CaptureHandle:
    return cv2.VideoCapture(index, backend)


def probe_camera_index(
    index: int,
    *,
    capture_factory: CaptureFactory = _default_capture_factory,
) -> CameraProbeResult:
    """Try Media Foundation, then DirectShow, always releasing every handle."""

    errors: list[str] = []
    for backend in CameraBackend:
        capture: CaptureHandle | None = None
        try:
            capture = capture_factory(index, int(backend))
            if not capture.isOpened():
                continue
            capture.read()
            return CameraProbeResult(
                index=index,
                available=True,
                backend=backend.name,
                width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=float(capture.get(cv2.CAP_PROP_FPS)),
            )
        except Exception as error:  # Device APIs may raise backend-specific exception types.
            errors.append(f"{backend.name}: {error}")
        finally:
            if capture is not None:
                capture.release()

    return CameraProbeResult(
        index=index,
        available=False,
        backend=None,
        width=None,
        height=None,
        fps=None,
        error="; ".join(errors) or None,
    )


def probe_camera_range(
    indexes: Sequence[int],
    *,
    capture_factory: CaptureFactory = _default_capture_factory,
) -> list[CameraProbeResult]:
    return [probe_camera_index(index, capture_factory=capture_factory) for index in indexes]


def submit_camera_probe(
    indexes: Sequence[int],
    *,
    capture_factory: CaptureFactory = _default_capture_factory,
) -> tuple[ThreadPoolExecutor, Future[list[CameraProbeResult]]]:
    """Run probing on a worker thread so callers cannot accidentally block Qt."""

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-probe")
    future = executor.submit(probe_camera_range, tuple(indexes), capture_factory=capture_factory)
    return executor, future


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int, default=5)
    args = parser.parse_args()
    if args.start < 0 or args.stop <= args.start:
        parser.error("require 0 <= start < stop")

    executor, future = submit_camera_probe(range(args.start, args.stop))
    try:
        results = future.result()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    print(json.dumps([asdict(result) for result in results], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
