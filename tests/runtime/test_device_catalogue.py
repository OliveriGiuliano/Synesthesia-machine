"""Engine-owned device catalogue, identity, caching, and partial-failure tests."""

from __future__ import annotations

from concurrent.futures import Future
from typing import cast

from synesthesia_machine.contracts import (
    ENGINE_PROTOCOL_VERSION,
    DeviceCatalogue,
    DeviceCatalogueResponse,
    DeviceDescriptor,
    DeviceKind,
    QueryDeviceCatalogue,
)
from synesthesia_machine.media import CameraDevice, CameraEnumerationService
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.runtime.device_catalogue import (
    DeviceCatalogueService,
    SystemDeviceCatalogueService,
)
from synesthesia_machine.runtime.engine_server import (
    DuplexConnection,
    EngineServer,
    EventQueueWriter,
)
from synesthesia_machine.runtime.in_process_engine import InProcessEngineClient


class _CameraCatalogue:
    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.close_count = 0

    def enumerate_async(self, *, force_refresh: bool = False) -> Future[tuple[CameraDevice, ...]]:
        self.calls.append(force_refresh)
        future: Future[tuple[CameraDevice, ...]] = Future()
        future.set_result((CameraDevice("opencv:2", "Desk camera", 2, "MSMF", 1280, 720, 30.0),))
        return future

    def close(self) -> None:
        self.close_count += 1


class _PendingCameraCatalogue:
    def __init__(self) -> None:
        self.future: Future[tuple[CameraDevice, ...]] = Future()

    def cached(self) -> tuple[CameraDevice, ...]:
        return ()

    def enumerate_async(self, *, force_refresh: bool = False) -> Future[tuple[CameraDevice, ...]]:
        del force_refresh
        return self.future

    def close(self) -> None:
        return


class _FixedCatalogueService:
    def __init__(self, catalogue: DeviceCatalogue) -> None:
        self.value = catalogue
        self.refreshes: list[bool] = []
        self.close_count = 0

    def catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        self.refreshes.append(force_refresh)
        return self.value

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        return self.catalogue(force_refresh=force_refresh)

    def close(self) -> None:
        self.close_count += 1


def test_catalogue_combines_stable_ids_caches_and_reports_partial_failures() -> None:
    cameras = _CameraCatalogue()
    midi_calls = 0

    def midi_devices() -> tuple[DeviceDescriptor, ...]:
        nonlocal midi_calls
        midi_calls += 1
        return (DeviceDescriptor(DeviceKind.MIDI_OUTPUT, "raw-midi-1", "Loop output"),)

    def audio_failure() -> tuple[DeviceDescriptor, ...]:
        raise OSError("synthetic PortAudio enumeration failure")

    service = SystemDeviceCatalogueService(
        camera_service=cast(CameraEnumerationService, cameras),
        midi_enumerator=midi_devices,
        audio_enumerator=audio_failure,
    )
    first = service.catalogue()
    second = service.catalogue()
    refreshed = service.catalogue(force_refresh=True)

    assert first is second
    assert first.devices == (
        DeviceDescriptor(DeviceKind.CAMERA_INPUT, "opencv:2", "Desk camera"),
        DeviceDescriptor(DeviceKind.MIDI_OUTPUT, "raw-midi-1", "Loop output"),
    )
    assert first.errors == (
        (DeviceKind.AUDIO_OUTPUT, "OSError: synthetic PortAudio enumeration failure"),
    )
    assert refreshed == first
    assert cameras.calls == [False, True]
    assert midi_calls == 2
    service.close()
    assert cameras.close_count == 1


def test_initial_catalogue_does_not_wait_for_camera_probe_and_refreshes_after_completion() -> None:
    cameras = _PendingCameraCatalogue()
    service = SystemDeviceCatalogueService(
        camera_service=cast(CameraEnumerationService, cameras),
        midi_enumerator=lambda: (),
        audio_enumerator=lambda: (),
    )

    initial = service.catalogue()
    assert initial.devices == ()
    assert initial.pending_kinds == (DeviceKind.CAMERA_INPUT,)
    cameras.future.set_result(
        (CameraDevice("opencv:3", "Document camera", 3, "MSMF", 1920, 1080, 30.0),)
    )

    completed = service.catalogue()
    assert completed.devices == (
        DeviceDescriptor(DeviceKind.CAMERA_INPUT, "opencv:3", "Document camera"),
    )
    assert completed.pending_kinds == ()
    service.close()


def test_background_camera_failure_is_reported_by_the_next_catalogue_poll() -> None:
    cameras = _PendingCameraCatalogue()
    service = SystemDeviceCatalogueService(
        camera_service=cast(CameraEnumerationService, cameras),
        midi_enumerator=lambda: (),
        audio_enumerator=lambda: (),
    )

    assert service.catalogue().pending_kinds == (DeviceKind.CAMERA_INPUT,)
    cameras.future.set_exception(OSError("synthetic camera probe failure"))

    completed = service.catalogue()
    assert completed.pending_kinds == ()
    assert completed.errors == (
        (DeviceKind.CAMERA_INPUT, "OSError: synthetic camera probe failure"),
    )
    service.close()


def test_in_process_client_exposes_injected_catalogue_and_closes_its_owner() -> None:
    catalogue = DeviceCatalogue(
        (DeviceDescriptor(DeviceKind.AUDIO_OUTPUT, "", "System default", True),)
    )
    service = _FixedCatalogueService(catalogue)
    client = InProcessEngineClient(
        NodeRegistry(()),
        device_catalogue_service=cast(DeviceCatalogueService, service),
    )

    assert client.device_catalogue(force_refresh=True) == catalogue
    assert service.refreshes == [True]
    client.close()
    assert service.close_count == 1


def test_device_catalogue_query_uses_current_protocol_without_graph_revision() -> None:
    query = QueryDeviceCatalogue("request", True)

    assert query.force_refresh
    assert query.protocol_version == ENGINE_PROTOCOL_VERSION == 14


def test_engine_server_dispatches_device_query_without_active_graph_revision() -> None:
    class _Connection:
        def __init__(self) -> None:
            self.sent: list[object] = []

        def send(self, value: object) -> None:
            self.sent.append(value)

        def recv(self) -> object:
            raise AssertionError("not used")

        def poll(self, timeout: float = 0.0) -> bool:
            del timeout
            return False

        def close(self) -> None:
            return

    class _Events:
        def put_nowait(self, value: object) -> None:
            del value

        def close(self) -> None:
            return

    catalogue = DeviceCatalogue(
        (DeviceDescriptor(DeviceKind.CAMERA_INPUT, "opencv:4", "Overhead camera"),)
    )
    service = _FixedCatalogueService(catalogue)
    connection = _Connection()
    server = EngineServer(
        cast(DuplexConnection, connection),
        cast(EventQueueWriter, _Events()),
    )
    server._engine.close()
    server._engine = cast(
        InProcessEngineClient,
        service,
    )

    assert not server._dispatch(QueryDeviceCatalogue("catalogue", True))
    assert connection.sent == [DeviceCatalogueResponse("catalogue", catalogue)]
    assert service.refreshes == [True]
