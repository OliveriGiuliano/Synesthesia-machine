"""Minimal Qt application objects for the Phase 0 bootstrap proof."""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from synesthesia_machine import __version__


class MainWindow(QMainWindow):
    """Minimal native application shell; the node editor begins in Phase 2."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Synesthesia Machine {__version__}")
        self.resize(960, 600)

        status = QLabel("Phase 0 foundation is ready", self)
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(status)
        self.statusBar().showMessage("Engine stopped")


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Create or return the process-wide QApplication."""

    existing = QApplication.instance()
    if existing is not None:
        if isinstance(existing, QApplication):
            return existing
        msg = "A non-GUI QCoreApplication already exists"
        raise RuntimeError(msg)

    qt_argv = list(argv) if argv is not None else []
    application = QApplication(qt_argv)
    application.setApplicationName("Synesthesia Machine")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("Synesthesia Machine")
    return application
