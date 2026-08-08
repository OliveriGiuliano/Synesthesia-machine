"""Shared offscreen Qt fixtures for Phase 7 tests."""

import os
from collections.abc import Iterator
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = (
        QApplication(["synmachine-phase7-tests"])
        if instance is None
        else cast(QApplication, instance)
    )
    yield application
    application.processEvents()


@pytest.fixture(autouse=True)
def dispose_qt_objects(qapp: QApplication) -> Iterator[None]:
    yield
    QApplication.clipboard().clear()
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
