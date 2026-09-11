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
from synesthesia_machine.ui.translations import tr, trf

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
        self._row_by_node: dict[UUID, int] = {}
        self._last_row_values: dict[UUID, tuple[object, ...]] = {}

        self.summary = QLabel(
            trf(
                "60 FPS frame budget {budget:.2f} ms · warning above {warning:.2f} ms p95",
                budget=FRAME_BUDGET_MS,
                warning=FRAME_BUDGET_MS * WARNING_FRACTION,
            ),
            self,
        )
        self.summary.setAccessibleName(tr("Profiler frame budget summary"))
        self.freeze_button = QPushButton(tr("Freeze"), self)
        self.freeze_button.setCheckable(True)
        self.reset_button = QPushButton(tr("Reset"), self)
        self.copy_button = QPushButton(tr("Copy report"), self)
        self.heatmap_checkbox = QCheckBox(tr("Canvas heatmap"), self)
        self.heatmap_checkbox.setChecked(True)

        controls = QHBoxLayout()
        controls.addWidget(self.freeze_button)
        controls.addWidget(self.reset_button)
        controls.addWidget(self.copy_button)
        controls.addWidget(self.heatmap_checkbox)
        controls.addStretch(1)
        controls.addWidget(self.summary)

        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(tuple(tr(header) for header in self.HEADERS))
        self.table.setAccessibleName(tr("Per-node runtime profiler"))
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
        """Update displayed data in place unless frozen; return whether changed.

        Rows are keyed by node id: existing rows only rewrite the cells whose
        values moved, new nodes append rows, and nodes that disappeared drop
        theirs. This keeps a 250 ms profiler tick from tearing the table down
        and re-measuring every cell while the canvas is being painted.
        """

        if self.frozen:
            return False
        self._profiles = profiles
        self._names = dict(names or {})
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        warning_ms = FRAME_BUDGET_MS * WARNING_FRACTION
        self.table.setSortingEnabled(False)

        present = {profile.node_id for profile in profiles}
        for node_id in [node_id for node_id in self._row_by_node if node_id not in present]:
            row = self._row_by_node.pop(node_id)
            self._last_row_values.pop(node_id, None)
            self.table.removeRow(row)
        # removeRow shifts the rows below it up; rebuild the row map from the
        # table's current state before touching any remaining row.
        self._row_by_node = {}

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            node_id = _row_node_id(item)
            if node_id is not None and node_id in present:
                self._row_by_node[node_id] = row

        for profile in profiles:
            row = self._row_by_node.get(profile.node_id)
            if row is None:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._row_by_node[profile.node_id] = row
            self._update_row(profile, row, warning_ms)
        self.table.setSortingEnabled(True)
        self.table.sortItems(sort_column, sort_order)
        return True

    def _update_row(self, profile: NodeProfile, row: int, warning_ms: float) -> None:
        name = self._names.get(profile.node_id, str(profile.node_id)[:8])
        timings = (
            profile.last_duration_ms,
            profile.ema_duration_ms,
            profile.p50_duration_ms,
            profile.p95_duration_ms,
            profile.max_duration_ms,
        )
        signature = (
            name,
            *timings,
            profile.invocation_count,
            profile.error_count,
            profile.output_summary,
            profile.output_bytes,
        )
        if self._last_row_values.get(profile.node_id) == signature:
            return
        self._last_row_values[profile.node_id] = signature
        name_item = QTableWidgetItem(name)
        name_item.setToolTip(str(profile.node_id))
        name_item.setData(Qt.ItemDataRole.UserRole, str(profile.node_id))
        self.table.setItem(row, 0, name_item)
        for column, value in enumerate(timings, start=1):
            item = _NumericItem(value, f"{value:.3f}")
            if column == 4 and value > warning_ms:
                item.setForeground(QColor("#f0b44c"))
                item.setToolTip(
                    tr("This node uses more than half of a 60 FPS frame budget at p95.")
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

    @Slot()
    def copy_report(self) -> None:
        lines = ["\t".join(tr(header) for header in self.HEADERS)]
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
        self.freeze_button.setText(tr("Resume" if frozen else "Freeze"))

    def retranslate(self) -> None:
        self.summary.setText(
            trf(
                "60 FPS frame budget {budget:.2f} ms · warning above {warning:.2f} ms p95",
                budget=FRAME_BUDGET_MS,
                warning=FRAME_BUDGET_MS * WARNING_FRACTION,
            )
        )
        self.summary.setAccessibleName(tr("Profiler frame budget summary"))
        self._set_frozen_label(self.frozen)
        self.reset_button.setText(tr("Reset"))
        self.copy_button.setText(tr("Copy report"))
        self.heatmap_checkbox.setText(tr("Canvas heatmap"))
        self.table.setHorizontalHeaderLabels(tuple(tr(header) for header in self.HEADERS))
        self.table.setAccessibleName(tr("Per-node runtime profiler"))

    @Slot()
    def _reset(self) -> None:
        self._profiles = ()
        self.table.setRowCount(0)
        self.resetRequested.emit()


HEAT_BUCKET = 0.02


def _row_node_id(item: QTableWidgetItem) -> UUID | None:
    raw = item.data(Qt.ItemDataRole.UserRole)
    if isinstance(raw, str) and raw:
        try:
            return UUID(raw)
        except ValueError:
            return None
    return None


def profile_heat_levels(profiles: tuple[NodeProfile, ...]) -> dict[UUID, float]:
    """Map p95 timings to a stable 60 FPS frame-budget heat scale.

    Levels are quantized to 2% steps: the canvas heatmap guard then skips
    every node whose level did not cross a bucket boundary, so a profiler
    tick no longer invalidates the whole canvas.
    """

    levels: dict[UUID, float] = {}
    for profile in profiles:
        value = min(1.0, max(0.0, profile.p95_duration_ms / FRAME_BUDGET_MS))
        levels[profile.node_id] = round(value / HEAT_BUCKET) * HEAT_BUCKET
    return levels


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


__all__ = ["FRAME_BUDGET_MS", "ProfilerPanel", "profile_heat_levels"]
