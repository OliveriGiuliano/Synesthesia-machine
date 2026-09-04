"""Production process composition and crash-safe Qt supervision tests."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from synesthesia_machine.app import bootstrap
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NodeProfile,
    NotePreview,
    SourceStatus,
    ValuePreview,
)
from synesthesia_machine.graph import GraphSnapshot, ValidationReport
from synesthesia_machine.graph.validation import ValidationIssue, ValidationSeverity
from synesthesia_machine.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide Qt only to this module's focused supervision UI test."""

    instance = QApplication.instance()
    return (
        QApplication(["synmachine-process-supervision-tests"])
        if instance is None
        else cast(QApplication, instance)
    )


def _paths(root: Path) -> ApplicationPaths:
    data = root / "data"
    paths = ApplicationPaths(data, data / "logs", data / "recovery")
    paths.ensure_exists()
    return paths


def _snapshot_list() -> list[GraphSnapshot]:
    return []


@dataclass(slots=True)
class _SupervisionClient:
    engine_status: EngineStatus
    latest_valid_snapshot: GraphSnapshot | None = None
    activations: list[GraphSnapshot] = field(default_factory=_snapshot_list)
    restart_count: int = 0
    closed: bool = False

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        del demand_roots
        self.activations.append(snapshot)
        self.latest_valid_snapshot = snapshot
        return EngineActivation(snapshot.revision, ValidationReport(), True)

    def play(self, source_node_id: UUID | None = None) -> None:
        del source_node_id

    def pause(self, source_node_id: UUID | None = None) -> None:
        del source_node_id

    def resume(self, source_node_id: UUID | None = None) -> None:
        del source_node_id

    def stop(self, source_node_id: UUID | None = None) -> None:
        del source_node_id

    def reload(self, source_node_id: UUID | None = None) -> None:
        del source_node_id

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        del source_node_id, source_time_s

    def panic(self) -> None:
        return

    def clear_previews(self) -> None:
        return

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        del source_node_id
        return ()

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        del output_node_id
        return ()

    def device_catalogue(self, *, force_refresh: bool = False) -> DeviceCatalogue:
        del force_refresh
        return DeviceCatalogue()

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        del node_id
        return ()

    def metrics(self) -> EngineMetrics:
        return EngineMetrics(
            EngineState.STOPPED,
            graph_revision=self.engine_status.graph_revision,
            restart_count=self.restart_count,
            child_process_id=self.engine_status.child_process_id,
        )

    def node_profiles(self) -> tuple[NodeProfile, ...]:
        return ()

    def set_profiling_enabled(self, enabled: bool) -> None:
        del enabled

    def reset_profiling(self) -> None:
        return

    def poll_image_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ImagePreview, ...]:
        del after_sequences
        return ()

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        del after_sequences
        return ()

    def poll_value_previews(
        self, after_sequences: Mapping[tuple[UUID, str], int] | None = None
    ) -> tuple[ValuePreview, ...]:
        del after_sequences
        return ()

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def status(self) -> EngineStatus:
        return self.engine_status

    def restart(self) -> EngineActivation | None:
        self.restart_count += 1
        self.engine_status = EngineStatus(
            EngineConnectionState.CONNECTED,
            child_process_id=222,
            graph_revision=(
                None if self.latest_valid_snapshot is None else self.latest_valid_snapshot.revision
            ),
            restart_count=self.restart_count,
            crash_log_path=self.engine_status.crash_log_path,
        )
        if self.latest_valid_snapshot is None:
            return None
        return EngineActivation(
            self.latest_valid_snapshot.revision,
            ValidationReport(),
            True,
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("close_error", [None, TimeoutError("engine still stopping")])
def test_production_bootstrap_uses_process_client_and_freeze_support(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    close_error: TimeoutError | None,
) -> None:
    events: list[object] = []
    paths = _paths(tmp_path)

    class _Application:
        def exec(self) -> int:
            events.append("exec")
            return 0

        def quit(self) -> None:
            return

    class _Client:
        def __init__(self, *, crash_log_path: Path) -> None:
            events.append(("client", crash_log_path))

        def close(self) -> None:
            events.append("close")
            if close_error is not None:
                raise close_error

    class _Window:
        def __init__(
            self,
            registry: object,
            paths_arg: ApplicationPaths,
            client: object,
            **_: object,
        ) -> None:
            del registry, client
            assert paths_arg == paths
            events.append("window")

        def show(self) -> None:
            events.append("show")

    def record_freeze_support() -> None:
        events.append("freeze")

    def current_paths() -> ApplicationPaths:
        return paths

    monkeypatch.setattr(bootstrap, "freeze_support", record_freeze_support)
    monkeypatch.setattr(bootstrap.ApplicationPaths, "for_current_user", current_paths)

    def configure_logging(_path: Path) -> object:
        return SimpleNamespace(session_id="s")

    def create_application(_arguments: object) -> _Application:
        return _Application()

    monkeypatch.setattr(bootstrap, "configure_logging", configure_logging)
    monkeypatch.setattr(bootstrap, "create_application", create_application)
    monkeypatch.setattr(bootstrap, "create_application_registry", lambda: object())
    monkeypatch.setattr(bootstrap, "ENGINE_CLIENT_FACTORY", _Client)
    monkeypatch.setattr(bootstrap, "MainWindow", _Window)

    assert bootstrap.main(["synmachine"]) == 0

    assert events == [
        "freeze",
        ("client", paths.logs / "engine-crash.log"),
        "window",
        "show",
        "exec",
        "close",
    ]


def test_engine_crash_keeps_document_and_undo_history_then_restart_rebuilds(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = create_application_registry()
    crash_log = tmp_path / "engine-crash.log"
    client = _SupervisionClient(
        EngineStatus(
            EngineConnectionState.CRASHED,
            child_process_id=111,
            graph_revision=3,
            exit_code=7,
            last_error="Engine process exited with code 7",
            crash_log_path=str(crash_log),
        )
    )
    messages: list[tuple[str, str]] = []

    def record_critical(_parent: object, title: str, detail: str) -> None:
        messages.append((title, detail))

    monkeypatch.setattr(QMessageBox, "critical", record_critical)
    window = MainWindow(
        registry,
        _paths(tmp_path),
        client,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    try:
        node_id = window.session.add_node("synmachine.utility.number", (20.0, 30.0))
        snapshot = window.session.document.snapshot()
        client.latest_valid_snapshot = snapshot
        undo_count = window.session.undo_stack.count()

        window._refresh_engine_status()  # pyright: ignore[reportPrivateUsage]
        qapp.processEvents()

        assert window.session.document.node(node_id) is not None
        assert window.session.undo_stack.count() == undo_count
        assert window.action_registry.require("restart_engine").isEnabled()
        assert (
            "Engine CRASHED · STOPPED · exit 7" in window._engine_status.text()  # pyright: ignore[reportPrivateUsage]
        )
        assert "no crash report file" in window._engine_status.text()  # pyright: ignore[reportPrivateUsage]
        assert len(messages) == 1
        assert "remains open and editable" in messages[0][1]
        assert str(crash_log) not in messages[0][1]
        assert "No crash-report file was produced" in messages[0][1]
        assert "Native termination" in messages[0][1]

        window._refresh_engine_status()  # pyright: ignore[reportPrivateUsage]
        assert len(messages) == 1

        window.restart_engine()

        # The restart runs on the engine task pool; wait for it to drain.
        deadline = time.monotonic() + 10.0
        while window._engine_tasks_inflight:  # pyright: ignore[reportPrivateUsage]
            if time.monotonic() > deadline:
                pytest.fail("restart task did not complete in time")
            qapp.processEvents()
            time.sleep(0.02)

        assert client.restart_count == 1
        assert window.session.document.snapshot() == snapshot
        assert window.session.undo_stack.count() == undo_count
        assert not window.action_registry.require("restart_engine").isEnabled()
        assert "Engine STOPPED" in window._engine_status.text()  # pyright: ignore[reportPrivateUsage]
    finally:
        window.session.new_document()
        window.close()


def test_broken_graph_activation_reports_the_engine_as_stopped(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    class BrokenActivationClient(_SupervisionClient):
        def activate(
            self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
        ) -> EngineActivation:
            del demand_roots
            report = ValidationReport(
                issues=(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "unknown_node_type",
                        "Unknown node type",
                    ),
                )
            )
            return EngineActivation(snapshot.revision, report, False)

    client = BrokenActivationClient(EngineStatus(EngineConnectionState.CONNECTED))
    window = MainWindow(
        create_application_registry(),
        _paths(tmp_path),
        client,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    try:
        window._activate_graph()  # pyright: ignore[reportPrivateUsage]
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            qapp.processEvents()
            if "engine stopped" in window.statusBar().currentMessage():
                break
            time.sleep(0.01)

        assert window.statusBar().currentMessage() == "Graph has 1 error(s); engine stopped"
    finally:
        window.session.new_document()
        window.close()


def test_stopped_engine_skips_redundant_periodic_refreshes(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = create_application_registry()
    client = _SupervisionClient(EngineStatus(EngineConnectionState.CONNECTED, child_process_id=222))
    metrics_calls = 0
    original_metrics = _SupervisionClient.metrics

    def counting_metrics(self: _SupervisionClient) -> EngineMetrics:
        nonlocal metrics_calls
        metrics_calls += 1
        return original_metrics(self)

    monkeypatch.setattr(_SupervisionClient, "metrics", counting_metrics)
    window = MainWindow(
        registry,
        _paths(tmp_path),
        client,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    try:
        # Isolate the test from the real periodic timers and auto-activation.
        for timer_name in (
            "_preview_timer",
            "_metrics_timer",
            "_device_refresh_timer",
            "_autosave_timer",
            "_activation_timer",
        ):
            getattr(window, timer_name).stop()  # pyright: ignore[reportPrivateUsage]

        def settle_refresh() -> None:
            deadline = time.monotonic() + 5.0
            while (
                "refresh" in window._engine_tasks_inflight  # pyright: ignore[reportPrivateUsage]
                and time.monotonic() < deadline
            ):
                qapp.processEvents()
                time.sleep(0.01)

        window._refresh_engine_status()  # pyright: ignore[reportPrivateUsage]
        settle_refresh()
        assert metrics_calls == 1
        assert window._engine_known_stopped is True  # pyright: ignore[reportPrivateUsage]

        # A stopped engine publishes no data: the repeated ticks keep the cheap
        # liveness check but submit no further metrics round trips.
        for _ in range(5):
            window._refresh_engine_status()  # pyright: ignore[reportPrivateUsage]
            qapp.processEvents()
        assert metrics_calls == 1

        # A selected node still forces a refresh for its memory diagnostic.
        node_id = window.session.add_node("synmachine.utility.number", (10.0, 10.0))
        window.scene.node_items[node_id].setSelected(True)
        qapp.processEvents()
        window._refresh_engine_status()  # pyright: ignore[reportPrivateUsage]
        settle_refresh()
        assert metrics_calls == 2
    finally:
        window.session.new_document()
        window.close()
