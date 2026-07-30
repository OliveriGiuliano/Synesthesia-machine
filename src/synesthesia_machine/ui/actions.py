"""Central action registry for discoverable editor commands and shortcuts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QKeySequence


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

    def create(self, spec: ActionSpec, callback: Callable[[], Any]) -> QAction:
        if spec.key in self._actions:
            raise ValueError(f"Action already exists: {spec.key}")
        action = QAction(spec.text, self)
        action.setObjectName(f"action_{spec.key}")
        action.setStatusTip(spec.status_tip)
        action.setToolTip(spec.status_tip)
        if spec.shortcut is not None:
            action.setShortcut(QKeySequence(spec.shortcut))
        action.triggered.connect(callback)
        self._actions[spec.key] = action
        return action

    def register(self, key: str, action: QAction, *, status_tip: str) -> QAction:
        if key in self._actions:
            raise ValueError(f"Action already exists: {key}")
        action.setObjectName(f"action_{key}")
        action.setStatusTip(status_tip)
        action.setToolTip(status_tip)
        self._actions[key] = action
        return action

    def require(self, key: str) -> QAction:
        try:
            return self._actions[key]
        except KeyError as error:
            raise KeyError(f"Unknown action: {key}") from error

    def values(self) -> tuple[QAction, ...]:
        return tuple(self._actions.values())
