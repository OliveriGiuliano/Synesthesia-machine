"""Central action registry for discoverable editor commands and shortcuts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QKeySequence

from synesthesia_machine.ui.translations import tr


@dataclass(frozen=True, slots=True)
class ActionSpec:
    key: str
    text: str
    status_tip: str
    shortcut: str | QKeySequence.StandardKey | None = None


class ActionRegistry(QObject):
    """Create and retain application actions from one accessible registry."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._actions: dict[str, QAction] = {}
        self._sources: dict[str, tuple[str, str]] = {}

    def create(self, spec: ActionSpec, callback: Callable[[], Any]) -> QAction:
        if spec.key in self._actions:
            raise ValueError(f"Action already exists: {spec.key}")
        action = QAction(tr(spec.text), self)
        action.setObjectName(f"action_{spec.key}")
        action.setStatusTip(tr(spec.status_tip))
        action.setToolTip(tr(spec.status_tip))
        if spec.shortcut is not None:
            action.setShortcut(QKeySequence(spec.shortcut))
        action.triggered.connect(callback)
        self._actions[spec.key] = action
        self._sources[spec.key] = (spec.text, spec.status_tip)
        return action

    def register(
        self,
        key: str,
        action: QAction,
        *,
        status_tip: str,
        source_text: str | None = None,
    ) -> QAction:
        if key in self._actions:
            raise ValueError(f"Action already exists: {key}")
        action.setObjectName(f"action_{key}")
        source = action.text() if source_text is None else source_text
        action.setText(tr(source))
        action.setStatusTip(tr(status_tip))
        action.setToolTip(tr(status_tip))
        self._actions[key] = action
        self._sources[key] = (source, status_tip)
        return action

    def require(self, key: str) -> QAction:
        try:
            return self._actions[key]
        except KeyError as error:
            raise KeyError(f"Unknown action: {key}") from error

    def values(self) -> tuple[QAction, ...]:
        return tuple(self._actions.values())

    def retranslate(self) -> None:
        """Refresh authored action text after an application language change."""

        for key, action in self._actions.items():
            text, status_tip = self._sources[key]
            action.setText(tr(text))
            action.setStatusTip(tr(status_tip))
            action.setToolTip(tr(status_tip))
