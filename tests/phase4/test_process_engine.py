"""Spawned engine handshake, correlation, activation, and recovery tests."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from synesthesia_machine.contracts import EngineConnectionState, EngineState
from synesthesia_machine.contracts.engine_messages import ENGINE_PROTOCOL_VERSION
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.runtime import EngineProtocolError, ProcessEngineClient


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
def process_client() -> Iterator[ProcessEngineClient]:
    client = ProcessEngineClient(
        request_timeout_s=1.5,
        activation_timeout_s=5.0,
        heartbeat_timeout_s=1.5,
        close_timeout_s=0.5,
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
    assert crashed.last_error is not None
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
    assert restarted.child_process_id is not None
    assert restarted.child_process_id != original_process_id
