"""Typed editor preferences and resilient QSettings persistence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class EditorPreferences:
    autosave_delay_seconds: int = 60
    grid_snap_enabled: bool = False
    grid_size: float = 24.0
    recent_file_limit: int = 8

    def __post_init__(self) -> None:
        if not 10 <= self.autosave_delay_seconds <= 3600:
            raise ValueError("Autosave delay must be in the range 10..3600 seconds")
        if not math.isfinite(self.grid_size) or not 8.0 <= self.grid_size <= 128.0:
            raise ValueError("Grid size must be finite and in the range 8..128")
        if not 1 <= self.recent_file_limit <= 20:
            raise ValueError("Recent-file limit must be in the range 1..20")


class ApplicationSettingsStore:
    """Own typed preference/recent-file keys while leaving window state to MainWindow."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings

    def load_preferences(self) -> EditorPreferences:
        defaults = EditorPreferences()
        return EditorPreferences(
            autosave_delay_seconds=_integer(
                self.settings.value("editor/autosaveDelaySeconds"),
                defaults.autosave_delay_seconds,
                minimum=10,
                maximum=3600,
            ),
            grid_snap_enabled=_boolean(
                self.settings.value("editor/gridSnapEnabled"), defaults.grid_snap_enabled
            ),
            grid_size=_number(
                self.settings.value("editor/gridSize"),
                defaults.grid_size,
                minimum=8.0,
                maximum=128.0,
            ),
            recent_file_limit=_integer(
                self.settings.value("editor/recentFileLimit"),
                defaults.recent_file_limit,
                minimum=1,
                maximum=20,
            ),
        )

    def save_preferences(self, preferences: EditorPreferences) -> None:
        self.settings.setValue("editor/autosaveDelaySeconds", preferences.autosave_delay_seconds)
        self.settings.setValue("editor/gridSnapEnabled", preferences.grid_snap_enabled)
        self.settings.setValue("editor/gridSize", preferences.grid_size)
        self.settings.setValue("editor/recentFileLimit", preferences.recent_file_limit)
        self.settings.sync()

    def load_recent_files(self, limit: int) -> list[Path]:
        raw: object = self.settings.value("recentFiles", [])
        if not isinstance(raw, list):
            return []
        result: list[Path] = []
        for value in cast(list[object], raw):
            if not isinstance(value, str):
                continue
            path = Path(value).expanduser().resolve()
            if path.is_file() and path not in result:
                result.append(path)
            if len(result) == limit:
                break
        if [str(path) for path in result] != raw:
            self.settings.setValue("recentFiles", [str(path) for path in result])
        return result

    def remember_recent(self, path: Path, current: list[Path], limit: int) -> list[Path]:
        resolved = path.expanduser().resolve()
        result = [resolved, *(item for item in current if item != resolved)][:limit]
        self.settings.setValue("recentFiles", [str(item) for item in result])
        self.settings.sync()
        return result

    def clear_recent(self) -> None:
        self.settings.remove("recentFiles")
        self.settings.sync()


class PreferencesDialog(QDialog):
    def __init__(
        self,
        preferences: EditorPreferences,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.autosave_delay = QSpinBox(self)
        self.autosave_delay.setRange(10, 3600)
        self.autosave_delay.setSuffix(" s")
        self.autosave_delay.setValue(preferences.autosave_delay_seconds)
        self.grid_snap = QCheckBox("Snap moved nodes and groups to the grid", self)
        self.grid_snap.setChecked(preferences.grid_snap_enabled)
        self.grid_size = QDoubleSpinBox(self)
        self.grid_size.setRange(8.0, 128.0)
        self.grid_size.setDecimals(1)
        self.grid_size.setValue(preferences.grid_size)
        self.recent_limit = QSpinBox(self)
        self.recent_limit.setRange(1, 20)
        self.recent_limit.setValue(preferences.recent_file_limit)

        form = QFormLayout()
        form.addRow("Autosave after inactivity", self.autosave_delay)
        form.addRow("Grid spacing", self.grid_size)
        form.addRow("Recent graphs", self.recent_limit)
        form.addRow(self.grid_snap)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def preferences(self) -> EditorPreferences:
        return EditorPreferences(
            autosave_delay_seconds=self.autosave_delay.value(),
            grid_snap_enabled=self.grid_snap.isChecked(),
            grid_size=self.grid_size.value(),
            recent_file_limit=self.recent_limit.value(),
        )


def _integer(value: object, default: int, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        result = int(cast("int | float | str", value))
    except (TypeError, ValueError, OverflowError):
        return default
    return result if minimum <= result <= maximum else default


def _number(value: object, default: float, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(cast("int | float | str", value))
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) and minimum <= result <= maximum else default


def _boolean(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default
