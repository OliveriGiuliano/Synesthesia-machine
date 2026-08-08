"""Qt application factory and main-window facade."""

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from synesthesia_machine import __version__
from synesthesia_machine.ui.main_window import MainWindow

__all__ = ["MainWindow", "create_application"]


def _application_icon_path() -> Path:
    """Return the icon path for source and Nuitka standalone layouts."""

    return Path(__file__).resolve().parents[1] / "resources" / "synesthesia-machine.ico"


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
    icon_path = _application_icon_path()
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    return application
