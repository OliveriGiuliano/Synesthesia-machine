"""EngineClient-only editor activation, transport, status, preview, and close tests."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    ImagePreview,
    MidiOutputStatus,
    NodeMemoryDiagnostic,
    NoteActivity,
    NotePreview,
    SourceState,
    SourceStatus,
    freeze_uint8_preview,
)
from synesthesia_machine.graph import GraphSnapshot, ValidationReport
from synesthesia_machine.ui.main_window import MainWindow

SOURCE_A = UUID("00000000-0000-0000-0000-000000000701")
SOURCE_B = UUID("00000000-0000-0000-0000-000000000702")
IMAGE_NODE = UUID("00000000-0000-0000-0000-000000000703")
NOTE_NODE = UUID("00000000-0000-0000-0000-000000000704")


def _snapshot_list() -> list[GraphSnapshot]:
    return []


def _call_list() -> list[tuple[str, UUID | None]]:
    return []


def _status_map() -> dict[UUID, SourceStatus]:
    return {}


@dataclass(slots=True)
class _RecordingEngineClient:
    activations: list[GraphSnapshot] = field(default_factory=_snapshot_list)
    calls: list[tuple[str, UUID | None]] = field(default_factory=_call_list)
    statuses: dict[UUID, SourceStatus] = field(default_factory=_status_map)
    image_previews: tuple[ImagePreview, ...] = ()
    note_previews: tuple[NotePreview, ...] = ()
    memory_diagnostics: tuple[NodeMemoryDiagnostic, ...] = ()
    closed: bool = False

    def activate(
        self, snapshot: GraphSnapshot, *, demand_roots: Iterable[UUID] | None = None
    ) -> EngineActivation:
        del demand_roots
        self.activations.append(snapshot)
        return EngineActivation(snapshot.revision, ValidationReport(), True)

    def play(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("play", source_node_id))

    def pause(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("pause", source_node_id))

    def resume(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("resume", source_node_id))

    def stop(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("stop", source_node_id))

    def reload(self, source_node_id: UUID | None = None) -> None:
        self.calls.append(("reload", source_node_id))

    def seek(self, source_node_id: UUID, source_time_s: float) -> None:
        del source_time_s
        self.calls.append(("seek", source_node_id))

    def panic(self) -> None:
        self.calls.append(("panic", None))

    def source_status(self, source_node_id: UUID | None = None) -> tuple[SourceStatus, ...]:
        if source_node_id is not None:
            status = self.statuses.get(source_node_id)
            return () if status is None else (status,)
        return tuple(self.statuses[key] for key in sorted(self.statuses, key=str))

    def midi_output_status(
        self, output_node_id: UUID | None = None
    ) -> tuple[MidiOutputStatus, ...]:
        del output_node_id
        return ()

    def node_memory_diagnostics(
        self, node_id: UUID | None = None
    ) -> tuple[NodeMemoryDiagnostic, ...]:
        if node_id is None:
            return self.memory_diagnostics
        return tuple(
            diagnostic for diagnostic in self.memory_diagnostics if diagnostic.node_id == node_id
        )

    def metrics(self) -> EngineMetrics:
        return EngineMetrics(
            EngineState.RUNNING,
            graph_revision=4,
            processed_ticks=42,
            processed_fps=29.5,
            p95_node_time_ms=2.25,
            dropped_before_processing=3,
            memory_bytes=64 * 1024 * 1024,
        )

    def poll_image_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[ImagePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.image_previews
            if preview.sequence > thresholds.get(preview.node_id, 0)
        )

    def poll_note_previews(
        self, after_sequences: Mapping[UUID, int] | None = None
    ) -> tuple[NotePreview, ...]:
        thresholds = after_sequences or {}
        return tuple(
            preview
            for preview in self.note_previews
            if preview.sequence > thresholds.get(preview.node_id, 0)
        )

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        del timeout_s
        return True

    def status(self) -> EngineStatus:
        return EngineStatus(EngineConnectionState.CONNECTED, graph_revision=4)

    def restart(self) -> EngineActivation | None:
        snapshot = self.activations[-1] if self.activations else None
        if snapshot is None:
            return None
        return EngineActivation(snapshot.revision, ValidationReport(), True)

    def close(self) -> None:
        self.closed = True


def _paths(root: Path) -> ApplicationPaths:
    data = root / "data"
    return ApplicationPaths(data, data / "logs", data / "recovery")


@pytest.fixture
def runtime_window(
    qapp: QApplication, tmp_path: Path
) -> Iterator[tuple[MainWindow, _RecordingEngineClient]]:
    registry = create_application_registry()
    client = _RecordingEngineClient()
    window = MainWindow(
        registry,
        _paths(tmp_path),
        client,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        offer_recovery=False,
    )
    window.show()
    qapp.processEvents()
    yield window, client
    if not client.closed:
        window.session.new_document()
        window.close()


def _source_status(node_id: UUID, state: SourceState = SourceState.READY) -> SourceStatus:
    return SourceStatus(node_id, state, f"{node_id}.mp4")


def test_session_changes_debounce_graph_activation_through_client(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    assert window._activation_timer.isActive()
    assert client.activations == []

    QTest.qWait(140)

    assert len(client.activations) == 1
    assert len(client.activations[0].nodes) == 1


def test_transport_auto_targets_sole_source_resumes_paused_and_never_fans_out(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    source_a = window.session.add_node("synmachine.input.load_video", (0.0, 0.0))
    client.statuses[source_a] = _source_status(source_a)
    window.play()
    assert client.calls[-1] == ("play", source_a)

    client.statuses[source_a] = _source_status(source_a, SourceState.PAUSED)
    window.play()
    assert client.calls[-1] == ("resume", source_a)

    source_b = window.session.add_node("synmachine.input.load_video", (300.0, 0.0))
    client.statuses[source_b] = _source_status(source_b)
    count = len(client.calls)
    window.stop()
    assert len(client.calls) == count
    assert "Select one source" in window.statusBar().currentMessage()

    window.scene.select_node_ids({source_b})
    window.reload()
    assert client.calls[-1] == ("reload", source_b)

    window.scene.select_node_ids({source_a, source_b})
    count = len(client.calls)
    window.pause()
    assert len(client.calls) == count


def test_preview_and_metrics_polling_update_ui_with_sequence_coalescing(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    data = freeze_uint8_preview(np.full((2, 3, 3), 127, dtype=np.uint8))
    image = ImagePreview(IMAGE_NODE, 1, 7, 3, 2, 3, data)
    notes = NotePreview(NOTE_NODE, 1, 7, (NoteActivity(0, 60, 100),))
    client.image_previews = (image,)
    client.note_previews = (notes,)

    window._poll_previews()
    window._refresh_engine_status()

    assert window.preview_panel.image_widget.latest_preview is image
    assert window.preview_panel.note_widget.latest_preview is notes
    assert "sequence 1" in window.preview_panel.image_caption.text()
    assert window._image_sequences == {IMAGE_NODE: 1}
    assert window._note_sequences == {NOTE_NODE: 1}
    assert "42 ticks @ 29.5 FPS" in window._engine_status.text()
    assert "drops 3" in window._engine_status.text()


def test_selected_node_memory_diagnostic_is_published_to_inspector(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    hold_id = window.session.add_node("synmachine.image.hold_image", (0.0, 0.0))
    client.memory_diagnostics = (
        NodeMemoryDiagnostic(
            hold_id,
            estimated_retained_bytes=2 * 1024 * 1024,
            retained_bytes=1024 * 1024,
            retained_frame_count=1,
            capacity_frame_count=2,
            memory_limit_bytes=256 * 1024 * 1024,
        ),
    )
    window.scene.select_node_ids({hold_id})

    window._refresh_engine_status()

    labels = {label.text() for label in window.inspector.findChildren(QLabel)}
    assert "2.00 MiB" in labels
    assert "1.00 MiB (1/2 frames)" in labels
    assert "256.00 MiB" in labels


def test_panic_and_accepted_close_are_owned_by_injected_client(
    runtime_window: tuple[MainWindow, _RecordingEngineClient],
) -> None:
    window, client = runtime_window
    window.panic()
    assert client.calls[-1] == ("panic", None)

    window.close()

    assert client.closed
    assert not window._preview_timer.isActive()
    assert not window._metrics_timer.isActive()
