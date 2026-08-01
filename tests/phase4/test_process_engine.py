"""Spawned engine handshake, correlation, activation, and recovery tests."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from synesthesia_machine.contracts import EngineActivation, EngineConnectionState, EngineState
from synesthesia_machine.contracts.engine_messages import (
    ENGINE_PROTOCOL_VERSION,
    ActivateGraph,
    GraphActivationAcknowledged,
    GraphSnapshotPayload,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.graph.validation import ValidationReport
from synesthesia_machine.nodes import ResetReason
from synesthesia_machine.runtime import EngineProtocolError, ProcessEngineClient
from synesthesia_machine.runtime import engine_server as engine_server_module
from synesthesia_machine.runtime.engine_server import EngineServer


def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _child_process_ids() -> set[int]:
    return {child.pid for child in psutil.Process().children(recursive=True)}


def _scalar_document() -> GraphDocument:
    document = GraphDocument()
    document.add_node(
        "synmachine.utility.number",
        parameters={"number_type": "FLOAT", "float_value": 0.5},
    )
    return document


@pytest.fixture
def process_client(tmp_path: Path) -> Iterator[ProcessEngineClient]:
    client = ProcessEngineClient(
        request_timeout_s=1.5,
        activation_timeout_s=5.0,
        heartbeat_timeout_s=1.5,
        close_timeout_s=0.5,
        crash_log_path=tmp_path / "engine-crash.log",
    )
    yield client
    client.close()


def test_spawn_handshake_heartbeat_and_idempotent_close() -> None:
    client = ProcessEngineClient(close_timeout_s=0.5)
    status = client.status()
    child_process_id = status.child_process_id

    assert status.connection_state is EngineConnectionState.CONNECTED
    assert child_process_id is not None and child_process_id != psutil.Process().pid
    assert psutil.pid_exists(child_process_id)
    assert _wait_until(lambda: client.status().last_heartbeat_monotonic_ns is not None)

    client.close()
    client.close()

    assert client.status().connection_state is EngineConnectionState.CLOSED
    assert _wait_until(lambda: not psutil.pid_exists(child_process_id))


def test_protocol_mismatch_is_rejected_and_child_is_reaped() -> None:
    children_before = _child_process_ids()
    client = ProcessEngineClient(
        auto_start=False,
        protocol_version=ENGINE_PROTOCOL_VERSION + 1,
        request_timeout_s=1.0,
        close_timeout_s=0.5,
    )
    try:
        with pytest.raises(EngineProtocolError, match="protocol mismatch"):
            client.start()
    finally:
        client.close()

    assert _wait_until(lambda: _child_process_ids() <= children_before)


def test_process_activation_metrics_and_concurrent_request_correlation(
    process_client: ProcessEngineClient,
) -> None:
    snapshot = _scalar_document().snapshot()

    activation = process_client.activate(snapshot)

    assert activation.activated and activation.report.is_valid
    assert activation.graph_revision == snapshot.revision
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = tuple(executor.submit(process_client.metrics) for _ in range(12))
        metrics = tuple(future.result() for future in futures)
    assert all(item.graph_revision == snapshot.revision for item in metrics)
    child_process_id = process_client.status().child_process_id
    assert all(item.child_process_id == child_process_id for item in metrics)
    assert process_client.source_status() == ()


def test_invalid_candidate_does_not_replace_active_revision(
    process_client: ProcessEngineClient,
) -> None:
    document = _scalar_document()
    valid_snapshot = document.snapshot()
    assert process_client.activate(valid_snapshot).activated

    document.add_node("unknown.node")
    invalid_snapshot = document.snapshot()
    rejected = process_client.activate(invalid_snapshot)

    assert not rejected.activated
    assert not rejected.report.is_valid
    assert rejected.graph_revision == invalid_snapshot.revision
    assert process_client.status().graph_revision == valid_snapshot.revision
    assert process_client.metrics().graph_revision == valid_snapshot.revision


def test_forced_crash_fails_boundedly_and_restart_rebuilds_latest_valid_graph(
    process_client: ProcessEngineClient,
) -> None:
    snapshot = _scalar_document().snapshot()
    assert process_client.activate(snapshot).activated
    original_process_id = process_client.status().child_process_id
    assert original_process_id is not None

    process_client.force_terminate()

    crashed = process_client.status()
    assert crashed.connection_state is EngineConnectionState.CRASHED
    assert crashed.exit_code is not None
    assert crashed.last_error is not None
    assert crashed.crash_log_path is not None
    assert not Path(crashed.crash_log_path).exists()
    assert _wait_until(lambda: not psutil.pid_exists(original_process_id))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"not connected|disconnected|exited"):
        process_client.source_status()
    assert time.monotonic() - started < 0.5
    assert process_client.metrics().state is EngineState.ERROR

    activation = process_client.restart()
    restarted = process_client.status()

    assert activation is not None and activation.activated
    assert activation.graph_revision == snapshot.revision
    assert restarted.connection_state is EngineConnectionState.CONNECTED
    assert restarted.graph_revision == snapshot.revision
    assert restarted.restart_count == 1
    assert restarted.exit_code is None
    assert restarted.crash_log_path == crashed.crash_log_path
    assert restarted.child_process_id is not None
    assert restarted.child_process_id != original_process_id


def test_start_clears_stale_crash_log_and_reports_configured_path(tmp_path: Path) -> None:
    crash_log = tmp_path / "logs" / "engine-crash.log"
    crash_log.parent.mkdir()
    crash_log.write_text("stale traceback", encoding="utf-8")

    client = ProcessEngineClient(crash_log_path=crash_log, close_timeout_s=0.5)
    try:
        status = client.status()

        assert status.connection_state is EngineConnectionState.CONNECTED
        assert status.crash_log_path == str(crash_log.resolve())
        assert not crash_log.exists()
    finally:
        client.close()


def test_child_target_writes_uncaught_exception_to_crash_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _CrashingServer:
        def __init__(self, connection: object, event_queue: object) -> None:
            del connection, event_queue

        def run(self) -> None:
            raise RuntimeError("deliberate child failure")

    class _CloseProbe:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(engine_server_module, "EngineServer", _CrashingServer)
    connection = _CloseProbe()
    event_queue = _CloseProbe()
    crash_log = tmp_path / "nested" / "engine-crash.log"

    with pytest.raises(RuntimeError, match="deliberate child failure"):
        engine_server_module.engine_server_main(
            connection,  # type: ignore[arg-type]
            event_queue,  # type: ignore[arg-type]
            str(crash_log),
        )

    detail = crash_log.read_text(encoding="utf-8")
    assert "RuntimeError: deliberate child failure" in detail
    assert connection.closed
    assert event_queue.closed


def test_server_forwards_activate_reset_reason_to_child_engine() -> None:
    snapshot = _scalar_document().snapshot()
    reset_reasons: list[ResetReason] = []

    class EngineProbe:
        def activate(
            self,
            snapshot_arg: object,
            *,
            demand_roots: object,
            reset_reason: ResetReason,
        ) -> EngineActivation:
            del snapshot_arg, demand_roots
            reset_reasons.append(reset_reason)
            return EngineActivation(snapshot.revision, ValidationReport(), True)

    class EventsProbe:
        def graph_activated(self, graph_revision: int) -> None:
            assert graph_revision == snapshot.revision

    server = object.__new__(EngineServer)
    server._engine = EngineProbe()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    server._events = EventsProbe()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    server._graph_revision = None  # pyright: ignore[reportPrivateUsage]
    command = ActivateGraph(
        "request",
        snapshot.revision,
        GraphSnapshotPayload.from_snapshot(snapshot),
        reset_reason=ResetReason.ENGINE_RESTARTED,
    )

    response = server._handle(command)  # pyright: ignore[reportPrivateUsage]

    assert isinstance(response, GraphActivationAcknowledged)
    assert response.activation.activated
    assert reset_reasons == [ResetReason.ENGINE_RESTARTED]
