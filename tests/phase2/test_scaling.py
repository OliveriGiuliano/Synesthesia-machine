"""Subprocess checks for the required Windows-style display scale factors."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scale", (1.0, 1.25, 1.5, 2.0))
def test_editor_shell_honours_qt_display_scaling(scale: float, tmp_path: Path) -> None:
    script = """
import os
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.ui.main_window import MainWindow

root = Path(os.environ["SYNMACHINE_SCALE_TEST_ROOT"])
app = QApplication([])
window = MainWindow(
    create_utility_registry(),
    ApplicationPaths(root, root / "logs", root / "recovery"),
    settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat),
    offer_recovery=False,
)
window.show()
app.processEvents()
print(f"{window.devicePixelRatioF()} {window.view.devicePixelRatioF()}")
window.close()
"""
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = str(scale)
    environment["SYNMACHINE_SCALE_TEST_ROOT"] = str(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    ratios = tuple(float(value) for value in completed.stdout.strip().split())
    assert ratios == pytest.approx((scale, scale))
