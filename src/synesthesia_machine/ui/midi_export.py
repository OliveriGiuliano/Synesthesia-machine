"""File > Export MIDI: run the graph offline and save a Standard MIDI File.

The UI thread only owns the dimmed overlay dialog and the progress
projection; the simulation itself runs in a worker thread on a transient
in-process engine (see :mod:`synesthesia_machine.runtime.midi_export` and
ADR-0016). The export registry is hardware-free, so no MIDI port, note, or
audio can be produced while exporting.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph.model import GraphSnapshot
from synesthesia_machine.midi import NullDebugSynth, NullMidiOutputService
from synesthesia_machine.nodes import ExecutionKind
from synesthesia_machine.nodes.input import LOAD_CAMERA_TYPE_ID, LOAD_VIDEO_TYPE_ID
from synesthesia_machine.nodes.output import SEND_MIDI_TYPE_ID
from synesthesia_machine.nodes.registry import NodeRegistry
from synesthesia_machine.runtime import MidiExportError, run_midi_export
from synesthesia_machine.ui.translations import tr, trf

if TYPE_CHECKING:
    from synesthesia_machine.ui.main_window import MainWindow


def midi_export_eligibility(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    *,
    graph_valid: bool,
) -> tuple[bool, str]:
    """Why the Export MIDI action is (not) usable, as a stable reason code."""

    saw_source = False
    all_video = True
    missing_media = False
    looping = False
    has_midi_output = False
    for node in snapshot.nodes:
        definition = registry.get(node.type_id)
        if definition is None:
            continue
        if definition.type_id == SEND_MIDI_TYPE_ID:
            has_midi_output = True
        if definition.execution_kind is not ExecutionKind.SOURCE:
            continue
        saw_source = True
        if definition.type_id == LOAD_CAMERA_TYPE_ID:
            all_video = False
        elif definition.type_id == LOAD_VIDEO_TYPE_ID:
            file_path = node.parameters.get("file_path")
            if not (isinstance(file_path, str) and file_path and Path(file_path).is_file()):
                missing_media = True
            elif bool(node.parameters.get("loop", False)):
                looping = True
    if not saw_source:
        return False, "no_source"
    if not all_video:
        return False, "camera_source"
    if missing_media:
        return False, "missing_media"
    if looping:
        return False, "looping_source"
    if not has_midi_output:
        return False, "no_midi_output"
    if not graph_valid:
        return False, "invalid_graph"
    return True, ""


_MIDI_EXPORT_FAILURE_TEXTS = {
    "cancelled": tr("The MIDI export was cancelled."),
    "timeout": tr("The MIDI export did not finish in time."),
    "camera_source": tr("Camera sources cannot be exported: use only Load Video sources."),
    "missing_media": tr("A video file is missing; locate it to export MIDI."),
    "looping_source": tr("A looping video source cannot be exported: stop the loop first."),
    "source_error": tr("A video source failed during the export."),
    "no_source": tr("The graph has no source node to simulate."),
    "no_midi_output": tr("The graph has no Send MIDI node to export."),
    "unsupported_source": tr("A source type cannot be exported."),
    "activation_failed": tr("The graph could not be activated for the export."),
    "save_failed": tr("The MIDI file could not be saved."),
}


def midi_export_failure_text(code: str, detail: str) -> str:
    """User-facing, translated text for a failed export."""

    text = _MIDI_EXPORT_FAILURE_TEXTS.get(code)
    if text is not None:
        return text
    return trf("MIDI export failed: {detail}", detail=detail)


def midi_export_tooltip(reason: str) -> str:
    """Explain why the action is disabled (empty string when enabled)."""

    return {
        "invalid_graph": tr("Fix the graph errors to export MIDI."),
        "no_source": tr("Add a Load Video source to export MIDI."),
        "camera_source": tr("Camera sources cannot be exported: use only Load Video sources."),
        "missing_media": tr("Locate the missing video file to export MIDI."),
        "looping_source": tr("Stop the video loop to export MIDI."),
        "no_midi_output": tr("Add a Send MIDI node to export MIDI."),
    }.get(reason, "")


class MidiExportSignals(QObject):
    """Thread-safe bridge from the export worker thread to the UI."""

    progress = Signal(int, int)  # processed, total (total 0 = indeterminate)
    completed = Signal(str)  # file path
    failed = Signal(str, str)  # stable code, raw detail for logging


class MidiExportDialog(QDialog):
    """Dimmed, locked overlay with a small centred progress bar."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("midi_export_overlay")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 130);")
        self.setWindowTitle(tr("Export MIDI"))

        self.status_label = QLabel(tr("Exporting MIDI…"), self)
        self.status_label.setObjectName("midi_export_status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("midi_export_progress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(260)
        self.progress_bar.setFixedHeight(14)
        self.cancel_button = QPushButton(tr("Cancel"), self)
        self.cancel_button.setObjectName("midi_export_cancel")

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.progress_bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        parent.installEventFilter(self)
        self._track_parent()

    def _track_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect().translated(parent.frameGeometry().topLeft()))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Escape must not dismiss the overlay: the export keeps running and the
        # Cancel button is the only way back.
        if event.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        pass

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._track_parent()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
        ):
            self._track_parent()
        return super().eventFilter(watched, event)

    def set_progress(self, processed: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, processed * 100 // total))
            self.status_label.setText(
                trf(
                    "Exporting MIDI… {processed} of {total} frames",
                    processed=processed,
                    total=total,
                )
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText(tr("Exporting MIDI…"))


class MidiExportJob:
    """Owns the worker thread, the stop flag, and the dialog for one export."""

    def __init__(self, window: MainWindow, snapshot: GraphSnapshot, file_path: str) -> None:
        self.window = window
        self.file_path = file_path
        self.signals = MidiExportSignals()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.dialog = MidiExportDialog(window)
        self.dialog.cancel_button.clicked.connect(self.cancel)
        self.dialog.show()

    def _worker(self, snapshot: GraphSnapshot, file_path: str) -> None:
        registry = create_application_registry(
            midi_output_service_factory=NullMidiOutputService,
            synth_factory=NullDebugSynth,
        )
        try:
            result = run_midi_export(
                snapshot,
                registry=registry,
                progress=lambda p: self.signals.progress.emit(
                    p.processed, 0 if p.total is None else p.total
                ),
                stop_event=self._stop_event,
            )
        except MidiExportError as error:
            self.signals.failed.emit(error.code, error.message)
            return
        except Exception as error:
            self.signals.failed.emit("unexpected", str(error))
            return
        try:
            Path(file_path).write_bytes(result.to_standard_midi_file())
        except OSError as error:
            self.signals.failed.emit("save_failed", str(error))
            return
        self.signals.completed.emit(file_path)

    def start(self, snapshot: GraphSnapshot) -> None:
        self._thread = threading.Thread(
            target=self._worker, args=(snapshot, self.file_path), name="midi-export", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._stop_event.set()

    def close(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.dialog.close()


__all__ = [
    "MidiExportDialog",
    "MidiExportJob",
    "MidiExportSignals",
    "midi_export_eligibility",
    "midi_export_failure_text",
    "midi_export_tooltip",
]
