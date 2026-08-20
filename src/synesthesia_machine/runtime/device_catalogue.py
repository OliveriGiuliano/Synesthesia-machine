"""Engine-owned hardware catalogue assembled without importing Qt."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Protocol

from synesthesia_machine.contracts import (
    DeviceCatalogue,
    DeviceDescriptor,
    DeviceKind,
)
from synesthesia_machine.media import CameraDevice, CameraEnumerationService
from synesthesia_machine.midi import (
    enumerate_audio_output_devices,
    enumerate_midi_output_devices,
)

type DeviceEnumerator = Callable[[], tuple[DeviceDescriptor, ...]]


class DeviceCatalogueService(Protocol):
    def catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue: ...

    def close(self) -> None: ...


class SystemDeviceCatalogueService:
    """Cache one partial catalogue and refresh all device kinds on explicit request."""

    def __init__(
        self,
        *,
        camera_service: CameraEnumerationService | None = None,
        midi_enumerator: DeviceEnumerator = enumerate_midi_output_devices,
        audio_enumerator: DeviceEnumerator = enumerate_audio_output_devices,
    ) -> None:
        self._camera_service = camera_service or CameraEnumerationService()
        self._midi_enumerator = midi_enumerator
        self._audio_enumerator = audio_enumerator
        self._lock = threading.Lock()
        self._cached: DeviceCatalogue | None = None
        self._camera_error: str | None = None
        self._closed = False

    def catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        with self._lock:
            if self._closed:
                raise RuntimeError("device catalogue service is closed")
            if self._cached is not None and not force_refresh:
                return self._cached

        devices: list[DeviceDescriptor] = []
        errors: list[tuple[DeviceKind, str]] = []
        pending_kinds: list[DeviceKind] = []
        camera_refresh: Future[tuple[CameraDevice, ...]] | None = None
        camera_refresh_was_pending = False
        with self._lock:
            camera_error = self._camera_error
            if force_refresh:
                self._camera_error = None
        if camera_error is not None and not force_refresh:
            errors.append((DeviceKind.CAMERA_INPUT, camera_error))
        else:
            try:
                camera_refresh = self._camera_service.enumerate_async(force_refresh=force_refresh)
                if camera_refresh.done():
                    cameras = camera_refresh.result()
                else:
                    camera_refresh_was_pending = True
                    pending_kinds.append(DeviceKind.CAMERA_INPUT)
                    # The command loop must remain responsive while Windows camera APIs probe
                    # hardware. Return the last known list now; the UI polls this pending kind.
                    cameras = self._camera_service.cached()
                devices.extend(
                    DeviceDescriptor(DeviceKind.CAMERA_INPUT, camera.device_id, camera.display_name)
                    for camera in cameras
                )
            except Exception as error:
                errors.append((DeviceKind.CAMERA_INPUT, _error_text(error)))
        for kind, enumerate_devices in (
            (DeviceKind.MIDI_OUTPUT, self._midi_enumerator),
            (DeviceKind.AUDIO_OUTPUT, self._audio_enumerator),
        ):
            try:
                devices.extend(enumerate_devices())
            except Exception as error:
                errors.append((kind, _error_text(error)))
        catalogue = DeviceCatalogue(
            tuple(sorted(devices)),
            tuple(sorted(errors)),
            tuple(sorted(pending_kinds)),
        )
        with self._lock:
            if not self._closed:
                self._cached = catalogue
        if camera_refresh is not None and camera_refresh_was_pending:
            camera_refresh.add_done_callback(self._camera_refresh_completed)
        return catalogue

    def _camera_refresh_completed(self, future: Future[tuple[CameraDevice, ...]]) -> None:
        # Preserve background failures for the next poll and rebuild the combined catalogue.
        camera_error: str | None = None
        try:
            future.result()
        except Exception as error:
            camera_error = _error_text(error)
        with self._lock:
            if not self._closed:
                self._camera_error = camera_error
                self._cached = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._camera_service.close()


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


__all__ = ["DeviceCatalogueService", "SystemDeviceCatalogueService"]
