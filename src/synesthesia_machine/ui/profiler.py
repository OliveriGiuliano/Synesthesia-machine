"""Sortable, throttled presentation of bounded runtime node profiles."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synesthesia_machine.contracts import NodeProfile

FRAME_BUDGET_MS = 1000.0 / 60.0
WARNING_FRACTION = 0.5


class _NumericItem(QTableWidgetItem):
    def __init__(self, value: float | int, text: str) -> None:
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, float(value))
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(left, float) and isinstance(right, float):
            return left < right
        return super().__lt__(other)


class ProfilerPanel(QWidget):
    """Profiler table with local freeze, engine reset, copy, and canvas heatmap controls."""

    resetRequested = Signal()
    heatmapChanged = Signal(bool)

    HEADERS = (
        "Node",
        "Last ms",
        "EMA ms",
        "p50 ms",
        "p95 ms",
        "Max ms",
        "Calls",
        "Errors",
        "Output",
        "Bytes",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profiles: tuple[NodeProfile, ...] = ()
        self._names: dict[UUID, str] = {}

        self.summary = QLabel(
            f"60 FPS frame budget {FRAME_BUDGET_MS:.2f} ms · warning above "
            f"{FRAME_BUDGET_MS * WARNING_FRACTION:.2f} ms p95",
            self,
        )
        self.summary.setAccessibleName("Profiler frame budget summary")
        self.freeze_button = QPushButton("Freeze", self)
        self.freeze_button.setCheckable(True)
        self.reset_button = QPushButton("Reset", self)
        self.copy_button = QPushButton("Copy report", self)
        self.heatmap_checkbox = QCheckBox("Canvas heatmap", self)
        self.heatmap_checkbox.setChecked(True)

        controls = QHBoxLayout()
        controls.addWidget(self.freeze_button)
        controls.addWidget(self.reset_button)
        controls.addWidget(self.copy_button)
        controls.addWidget(self.heatmap_checkbox)
        controls.addStretch(1)
        controls.addWidget(self.summary)

        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAccessibleName("Per-node runtime profiler")
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addWidget(self.table, 1)

        self.freeze_button.toggled.connect(self._set_frozen_label)
        self.reset_button.clicked.connect(self._reset)
        self.copy_button.clicked.connect(self.copy_report)
        self.heatmap_checkbox.toggled.connect(self.heatmapChanged)

    @property
    def frozen(self) -> bool:
        return self.freeze_button.isChecked()

    @property
    def profiles(self) -> tuple[NodeProfile, ...]:
        return self._profiles

    def set_profiles(
        self,
        profiles: tuple[NodeProfile, ...],
        names: Mapping[UUID, str] | None = None,
    ) -> bool:
        """Replace displayed data unless frozen; return whether the view changed."""

        if self.frozen:
            return False
        self._profiles = profiles
        self._names = dict(names or {})
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(profiles))
        warning_ms = FRAME_BUDGET_MS * WARNING_FRACTION
        for row, profile in enumerate(profiles):
            name = self._names.get(profile.node_id, str(profile.node_id)[:8])
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(str(profile.node_id))
            self.table.setItem(row, 0, name_item)
            timings = (
                profile.last_duration_ms,
                profile.ema_duration_ms,
                profile.p50_duration_ms,
                profile.p95_duration_ms,
                profile.max_duration_ms,
            )
            for column, value in enumerate(timings, start=1):
                item = _NumericItem(value, f"{value:.3f}")
                if column == 4 and value > warning_ms:
                    item.setForeground(QColor("#f0b44c"))
                    item.setToolTip(
                        "This node uses more than half of a 60 FPS frame budget at p95."
                    )
                self.table.setItem(row, column, item)
            self.table.setItem(
                row, 6, _NumericItem(profile.invocation_count, f"{profile.invocation_count:,}")
            )
            error_item = _NumericItem(profile.error_count, f"{profile.error_count:,}")
            if profile.error_count:
                error_item.setForeground(QColor("#ef6b73"))
            self.table.setItem(row, 7, error_item)
            output_item = QTableWidgetItem(profile.output_summary)
            output_item.setToolTip(profile.output_summary)
            self.table.setItem(row, 8, output_item)
            self.table.setItem(
                row, 9, _NumericItem(profile.output_bytes, _format_bytes(profile.output_bytes))
            )
        self.table.setSortingEnabled(True)
        self.table.sortItems(sort_column, sort_order)
        return True

    @Slot()
    def copy_report(self) -> None:
        lines = ["\t".join(self.HEADERS)]
        for profile in self._profiles:
            lines.append(
                "\t".join(
                    (
                        self._names.get(profile.node_id, str(profile.node_id)),
                        f"{profile.last_duration_ms:.3f}",
                        f"{profile.ema_duration_ms:.3f}",
                        f"{profile.p50_duration_ms:.3f}",
                        f"{profile.p95_duration_ms:.3f}",
                        f"{profile.max_duration_ms:.3f}",
                        str(profile.invocation_count),
                        str(profile.error_count),
                        profile.output_summary,
                        str(profile.output_bytes),
                    )
                )
            )
        QApplication.clipboard().setText("\n".join(lines))

    @Slot(bool)
    def _set_frozen_label(self, frozen: bool) -> None:
        self.freeze_button.setText("Resume" if frozen else "Freeze")

    @Slot()
    def _reset(self) -> None:
        self._profiles = ()
        self.table.setRowCount(0)
        self.resetRequested.emit()


def profile_heat_levels(profiles: tuple[NodeProfile, ...]) -> dict[UUID, float]:
    """Map p95 timings to a stable 60 FPS frame-budget heat scale."""

    return {
        profile.node_id: min(1.0, max(0.0, profile.p95_duration_ms / FRAME_BUDGET_MS))
        for profile in profiles
    }


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


__all__ = ["FRAME_BUDGET_MS", "ProfilerPanel", "profile_heat_levels"]
