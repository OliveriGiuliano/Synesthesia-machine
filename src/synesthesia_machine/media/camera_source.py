"""OpenCV camera enumeration and reconnecting live capture without Qt ownership."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Protocol, cast
from uuid import UUID

import cv2
import numpy as np
from numpy.typing import NDArray

from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    NoData,
    SourceState,
    SourceStatus,
)
from synesthesia_machine.media.video_source import PresentedSourceFrame
from synesthesia_machine.nodes.base import ResetReason

_DEVICE_ID = re.compile(r"^opencv:(0|[1-9][0-9]*)$")
_NANOSECONDS_PER_SECOND = 1_000_000_000
_DEFAULT_RECONNECT_INITIAL_S = 0.25
_DEFAULT_RECONNECT_MAX_S = 5.0
_DEFAULT_ENUMERATION_CACHE_S = 5.0


class CameraBackendPreference(StrEnum):
    AUTO = "AUTO"
    MEDIA_FOUNDATION = "MEDIA_FOUNDATION"
    DIRECTSHOW = "DIRECTSHOW"


class CameraBackend(IntEnum):
    MEDIA_FOUNDATION = cv2.CAP_MSMF
    DIRECTSHOW = cv2.CAP_DSHOW


class CameraUnavailableError(RuntimeError):
    """Expected camera open failure after all allowed backends were attempted."""


class CameraCapture(Protocol):
    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, object]: ...

    def get(self, property_id: int, /) -> float: ...

    def set(self, property_id: int, value: float, /) -> bool: ...

    def release(self) -> None: ...


class CameraClock(Protocol):
    def monotonic_ns(self) -> int: ...

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool: ...


class SystemCameraClock:
    def monotonic_ns(self) -> int:
        return time.perf_counter_ns()

    def wait(self, wake_event: threading.Event, timeout_s: float | None) -> bool:
        return wake_event.wait(timeout_s)


@dataclass(frozen=True, slots=True)
class CameraDevice:
    """Cached selectable camera descriptor; OpenCV indexes are the portable fallback ID."""

    device_id: str
    display_name: str
    index: int
    backend: str
    width: int | None
    height: int | None
    fps: float | None


@dataclass(frozen=True, slots=True)
class OpenedCamera:
    capture: CameraCapture
    backend: CameraBackend
    width: int | None
    height: int | None
    fps: float | None


type CameraCaptureFactory = Callable[[int, int], CameraCapture]
type CameraFrameCallback = Callable[[PresentedSourceFrame], None]
type ResetCallback = Callable[[ResetReason], None]


def _default_capture_factory(index: int, backend: int) -> CameraCapture:
    return cv2.VideoCapture(index, backend)


def camera_index_from_device_id(device_id: str) -> int:
    match = _DEVICE_ID.fullmatch(device_id)
    if match is None:
        raise ValueError(
            f"Unsupported camera device ID {device_id!r}; "
            "select an enumerated ID such as 'opencv:0'"
        )
    return int(match.group(1))


def _backend_order(preference: CameraBackendPreference) -> tuple[CameraBackend, ...]:
    if preference is CameraBackendPreference.MEDIA_FOUNDATION:
        return (CameraBackend.MEDIA_FOUNDATION,)
    if preference is CameraBackendPreference.DIRECTSHOW:
        return (CameraBackend.DIRECTSHOW,)
    return (CameraBackend.MEDIA_FOUNDATION, CameraBackend.DIRECTSHOW)


def open_camera(
    index: int,
    *,
    backend_preference: CameraBackendPreference = CameraBackendPreference.AUTO,
    requested_width: int = 0,
    requested_height: int = 0,
    requested_fps: float = 0.0,
    capture_factory: CameraCaptureFactory = _default_capture_factory,
) -> OpenedCamera:
    """Open one exact index using the requested Windows backend policy."""

    if index < 0:
        raise ValueError("camera index cannot be negative")
    if requested_width < 0 or requested_height < 0 or requested_fps < 0.0:
        raise ValueError("requested camera dimensions and FPS cannot be negative")

    failures: list[str] = []
    for backend in _backend_order(backend_preference):
        capture: CameraCapture | None = None
        keep_open = False
        try:
            capture = capture_factory(index, int(backend))
            if not capture.isOpened():
                failures.append(f"{backend.name}: unavailable")
                continue
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1.0)
            if requested_width > 0:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(requested_width))
            if requested_height > 0:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(requested_height))
            if requested_fps > 0.0:
                capture.set(cv2.CAP_PROP_FPS, requested_fps)
            opened = OpenedCamera(
                capture,
                backend,
                _positive_int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                _positive_int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                _positive_float(capture.get(cv2.CAP_PROP_FPS)),
            )
            keep_open = True
            return opened
        except Exception as error:
            failures.append(f"{backend.name}: {type(error).__name__}: {error}")
        finally:
            if capture is not None and not keep_open:
                with suppress(Exception):
                    capture.release()
    details = "; ".join(failures) or "no capture backend was attempted"
    raise CameraUnavailableError(f"Camera index {index} is unavailable ({details})")


def enumerate_cameras(
    indexes: Sequence[int] = tuple(range(5)),
    *,
    capture_factory: CameraCaptureFactory = _default_capture_factory,
) -> tuple[CameraDevice, ...]:
    """Probe camera indexes with MSMF-to-DSHOW fallback and close every handle."""

    devices: list[CameraDevice] = []
    for index in indexes:
        try:
            opened = open_camera(index, capture_factory=capture_factory)
        except CameraUnavailableError:
            continue
        try:
            devices.append(
                CameraDevice(
                    device_id=f"opencv:{index}",
                    display_name=f"Camera {index}",
                    index=index,
                    backend=opened.backend.name,
                    width=opened.width,
                    height=opened.height,
                    fps=opened.fps,
                )
            )
        finally:
            opened.capture.release()
    return tuple(devices)


class CameraEnumerationService:
    """Serialize hardware probes on a background worker and cache their result."""

    def __init__(
        self,
        *,
        indexes: Sequence[int] = tuple(range(5)),
        capture_factory: CameraCaptureFactory = _default_capture_factory,
        cache_duration_s: float = _DEFAULT_ENUMERATION_CACHE_S,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        if cache_duration_s < 0.0:
            raise ValueError("camera enumeration cache duration cannot be negative")
        self._indexes = tuple(indexes)
        self._capture_factory = capture_factory
        self._cache_duration_ns = round(cache_duration_s * _NANOSECONDS_PER_SECOND)
        self._monotonic_ns = monotonic_ns
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-enumeration")
        self._cached: tuple[CameraDevice, ...] = ()
        self._cache_expires_ns = 0
        self._in_flight: Future[tuple[CameraDevice, ...]] | None = None
        self._closed = False

    def cached(self) -> tuple[CameraDevice, ...]:
        with self._lock:
            return self._cached

    def enumerate_async(self, *, force_refresh: bool = False) -> Future[tuple[CameraDevice, ...]]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Camera enumeration service is closed")
            now_ns = self._monotonic_ns()
            if not force_refresh and self._cache_expires_ns > now_ns:
                completed: Future[tuple[CameraDevice, ...]] = Future()
                completed.set_result(self._cached)
                return completed
            if self._in_flight is not None and not self._in_flight.done():
                return self._in_flight
            future = self._executor.submit(self._refresh)
            self._in_flight = future
            return future

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _refresh(self) -> tuple[CameraDevice, ...]:
        devices = enumerate_cameras(self._indexes, capture_factory=self._capture_factory)
        with self._lock:
            self._cached = devices
            self._cache_expires_ns = self._monotonic_ns() + self._cache_duration_ns
        return devices


class CameraSourceService:
    """Own one exact live camera and reconnect it without blocking UI or graph threads."""

    def __init__(
        self,
        node_id: UUID,
        device_id: str,
        *,
        requested_width: int = 1280,
        requested_height: int = 720,
        requested_fps: float = 30.0,
        backend_preference: CameraBackendPreference = CameraBackendPreference.AUTO,
        process_every_nth_frame: int = 1,
        reconnect_automatically: bool = True,
        on_frame: CameraFrameCallback,
        on_reset: ResetCallback | None = None,
        capture_factory: CameraCaptureFactory = _default_capture_factory,
        clock: CameraClock | None = None,
        reconnect_initial_s: float = _DEFAULT_RECONNECT_INITIAL_S,
        reconnect_max_s: float = _DEFAULT_RECONNECT_MAX_S,
    ) -> None:
        if requested_width < 1 or requested_height < 1 or requested_fps <= 0.0:
            raise ValueError("requested camera dimensions and FPS must be positive")
        if process_every_nth_frame < 1:
            raise ValueError("process_every_nth_frame must be at least 1")
        if reconnect_initial_s <= 0.0 or reconnect_max_s < reconnect_initial_s:
            raise ValueError("camera reconnect delays must be positive and ordered")

        self.node_id = node_id
        self._device_id = device_id
        self._index = camera_index_from_device_id(device_id)
        self._display_name = f"Camera {self._index}"
        self._requested_width = requested_width
        self._requested_height = requested_height
        self._requested_fps = requested_fps
        self._backend_preference = backend_preference
        self._process_every_nth_frame = process_every_nth_frame
        self._reconnect_automatically = reconnect_automatically
        self._on_frame = on_frame
        self._on_reset = on_reset
        self._capture_factory = capture_factory
        self._clock = clock or SystemCameraClock()
        self._reconnect_initial_s = reconnect_initial_s
        self._reconnect_max_s = reconnect_max_s

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: CameraCapture | None = None
        self._state = SourceState.READY
        self._backend: str | None = None
        self._width: int | None = None
        self._height: int | None = None
        self._negotiated_fps: float | None = None
        self._source_frame_index: int | None = None
        self._processed_index = 0
        self._skipped_by_selection = 0
        self._warnings = 0
        self._last_error: str | None = None
        self._reconnect_attempts = 0
        self._run_started_ns: int | None = None

    def status(self, *, dropped_before_processing: int = 0) -> SourceStatus:
        with self._lock:
            return SourceStatus(
                node_id=self.node_id,
                state=self._state,
                width=self._width,
                height=self._height,
                source_frame_index=self._source_frame_index,
                processed_index=self._processed_index,
                skipped_by_selection=self._skipped_by_selection,
                dropped_before_processing=dropped_before_processing,
                warnings=self._warnings,
                last_error=self._last_error,
                source_kind="camera",
                display_name=self._display_name,
                device_id=self._device_id,
                backend=self._backend,
                negotiated_fps=self._negotiated_fps,
                reconnect_attempts=self._reconnect_attempts,
                requested_width=self._requested_width,
                requested_height=self._requested_height,
                requested_fps=self._requested_fps,
            )

    def play(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                raise RuntimeError("Camera source is closed")
            if self._state is SourceState.PAUSED:
                self._state = SourceState.PLAYING
                self._wake_event.set()
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._state = SourceState.PLAYING
            self._source_frame_index = None
            self._processed_index = 0
            self._skipped_by_selection = 0
            self._reconnect_attempts = 0
            self._last_error = None
            self._run_started_ns = self._clock.monotonic_ns()
            thread = threading.Thread(
                target=self._run,
                name=f"camera-capture-{self.node_id}",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._state in {SourceState.PLAYING, SourceState.RECONNECTING}:
                self._state = SourceState.PAUSED
        self._wake_event.set()

    def resume(self) -> None:
        with self._lock:
            if self._state is SourceState.PAUSED:
                self._state = SourceState.PLAYING
        self._wake_event.set()

    def stop(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                return
        self._halt(SourceState.STOPPED)
        self._publish_reset(ResetReason.SOURCE_RESTARTED)

    def reload(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                raise RuntimeError("Camera source is closed")
        self._halt(SourceState.READY)
        self._publish_reset(ResetReason.SOURCE_RESTARTED)

    def seek(self, source_time_s: float) -> None:
        del source_time_s
        raise NotImplementedError("Live camera sources cannot seek")

    def wait_until_finished(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def close(self) -> None:
        with self._lock:
            if self._state is SourceState.CLOSED:
                return
        self._halt(SourceState.CLOSED)

    def _halt(self, target_state: SourceState) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            capture = self._capture
            thread = self._thread
        if capture is not None:
            with suppress(Exception):
                capture.release()
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            self._capture = None
            if self._thread is not threading.current_thread():
                self._thread = None
            self._state = target_state
            self._source_frame_index = None
            self._processed_index = 0
            self._run_started_ns = None

    def _run(self) -> None:
        reconnect_delay_s = self._reconnect_initial_s
        connected_once = False
        while not self._stop_event.is_set():
            if not self._wait_until_active():
                return
            try:
                opened = open_camera(
                    self._index,
                    backend_preference=self._backend_preference,
                    requested_width=self._requested_width,
                    requested_height=self._requested_height,
                    requested_fps=self._requested_fps,
                    capture_factory=self._capture_factory,
                )
            except CameraUnavailableError as error:
                if not self._handle_disconnect(str(error)):
                    return
                if self._clock.wait(self._stop_event, reconnect_delay_s):
                    return
                reconnect_delay_s = min(reconnect_delay_s * 2.0, self._reconnect_max_s)
                continue

            with self._lock:
                self._capture = opened.capture
                self._backend = opened.backend.name
                self._width = opened.width
                self._height = opened.height
                self._negotiated_fps = opened.fps
                self._last_error = None
                self._state = SourceState.PLAYING
            if connected_once or self._reconnect_attempts > 0:
                self._publish_reset(ResetReason.SOURCE_RESTARTED)
            connected_once = True
            reconnect_delay_s = self._reconnect_initial_s
            disconnect_error = self._capture_until_disconnect(opened.capture)
            with self._lock:
                if self._capture is opened.capture:
                    self._capture = None
            with suppress(Exception):
                opened.capture.release()
            if disconnect_error is None or self._stop_event.is_set():
                return
            if not self._handle_disconnect(disconnect_error):
                return
            if self._clock.wait(self._stop_event, reconnect_delay_s):
                return
            reconnect_delay_s = min(reconnect_delay_s * 2.0, self._reconnect_max_s)

    def _capture_until_disconnect(self, capture: CameraCapture) -> str | None:
        while not self._stop_event.is_set():
            if not self._wait_until_active():
                return None
            try:
                available, raw_frame = capture.read()
            except Exception as error:
                return f"Camera capture failed: {type(error).__name__}: {error}"
            received_ns = self._clock.monotonic_ns()
            if not available:
                return "Camera disconnected while capturing"
            try:
                rgb = _bgr_to_rgb_float32(raw_frame)
            except (TypeError, ValueError) as error:
                return f"Camera returned an invalid frame: {error}"

            with self._lock:
                source_frame_index = (
                    0 if self._source_frame_index is None else self._source_frame_index + 1
                )
                self._source_frame_index = source_frame_index
                if source_frame_index % self._process_every_nth_frame != 0:
                    self._skipped_by_selection += 1
                    continue
                self._processed_index += 1
                processed_index = self._processed_index
                run_started_ns = (
                    received_ns if self._run_started_ns is None else self._run_started_ns
                )
            context = FrameContext(
                clock_id=self.node_id,
                tick_index=processed_index,
                source_frame_index=source_frame_index,
                source_time_s=max(0.0, (received_ns - run_started_ns) / _NANOSECONDS_PER_SECOND),
                received_monotonic_ns=received_ns,
                deadline_monotonic_ns=None,
                is_realtime=True,
            )
            image = ImageFrame(
                rgb,
                ColorSpace.SRGB,
                ("R", "G", "B"),
                AlphaMode.NONE,
                context,
                FrameProvenance(self.node_id, "camera"),
            )
            if not self._publish(PresentedSourceFrame(image, processed_index)):
                return None
        return None

    def _handle_disconnect(self, message: str) -> bool:
        with self._lock:
            self._warnings += 1
            self._reconnect_attempts += 1
            self._last_error = message
            self._state = (
                SourceState.RECONNECTING
                if self._reconnect_automatically
                else SourceState.UNAVAILABLE
            )
        self._publish_outage()
        return self._reconnect_automatically and not self._stop_event.is_set()

    def _publish_outage(self) -> None:
        received_ns = self._clock.monotonic_ns()
        with self._lock:
            self._processed_index += 1
            processed_index = self._processed_index
            run_started_ns = received_ns if self._run_started_ns is None else self._run_started_ns
        context = FrameContext(
            clock_id=self.node_id,
            tick_index=processed_index,
            source_frame_index=None,
            source_time_s=max(0.0, (received_ns - run_started_ns) / _NANOSECONDS_PER_SECOND),
            received_monotonic_ns=received_ns,
            deadline_monotonic_ns=None,
            is_realtime=True,
        )
        self._publish(PresentedSourceFrame(NoData, processed_index, context))

    def _publish(self, frame: PresentedSourceFrame) -> bool:
        try:
            self._on_frame(frame)
        except Exception as error:
            with self._lock:
                self._warnings += 1
                self._last_error = f"Camera frame publication failed: {error}"
                self._state = SourceState.ERROR
            self._stop_event.set()
            self._wake_event.set()
            return False
        return True

    def _wait_until_active(self) -> bool:
        while not self._stop_event.is_set():
            with self._lock:
                if self._state in {SourceState.PLAYING, SourceState.RECONNECTING}:
                    return True
                if self._state is not SourceState.PAUSED:
                    return False
            self._wake_event.clear()
            self._clock.wait(self._wake_event, None)
        return False

    def _publish_reset(self, reason: ResetReason) -> None:
        if self._on_reset is not None:
            self._on_reset(reason)


def _positive_int(value: float) -> int | None:
    return rounded if (rounded := round(value)) > 0 else None


def _positive_float(value: float) -> float | None:
    return float(value) if np.isfinite(value) and value > 0.0 else None


def _bgr_to_rgb_float32(raw_frame: object) -> NDArray[np.float32]:
    if not isinstance(raw_frame, np.ndarray):
        raise TypeError("capture frame is not an ndarray")
    frame = cast(NDArray[np.uint8], raw_frame)
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"expected HxWx3 uint8 BGR, got {frame.shape} {frame.dtype}")
    rgb = np.ascontiguousarray(frame[..., ::-1], dtype=np.float32)
    rgb *= np.float32(1.0 / 255.0)
    rgb.flags.writeable = False
    return rgb


__all__ = [
    "CameraBackend",
    "CameraBackendPreference",
    "CameraCapture",
    "CameraCaptureFactory",
    "CameraClock",
    "CameraDevice",
    "CameraEnumerationService",
    "CameraSourceService",
    "CameraUnavailableError",
    "OpenedCamera",
    "SystemCameraClock",
    "camera_index_from_device_id",
    "enumerate_cameras",
    "open_camera",
]
