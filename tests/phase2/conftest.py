"""Shared offscreen Qt fixtures for Phase 2 editor tests."""

import os
from collections.abc import Iterator
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp() -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = (
        QApplication(["synmachine-phase2-tests"])
        if instance is None
        else cast(QApplication, instance)
    )
    yield application
    application.processEvents()


@pytest.fixture(autouse=True)
def dispose_qt_objects(qapp: QApplication) -> Iterator[None]:
    """Destroy test-owned Qt objects while their QApplication is still alive."""

    yield
    QApplication.clipboard().clear()
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
