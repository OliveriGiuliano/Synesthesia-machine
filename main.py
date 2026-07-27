"""
Synesthesia Machine - Main Entry Point

A program that takes video input and outputs MIDI instructions.
Video frames are analyzed and mapped to musical notes based on
the selected synesthesia mode.

Usage:
    python main.py
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtCore import Qt
from synesthesia_machine.gui.main_window import MainWindow


def main():
    """Launch the Synesthesia Machine application."""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Synesthesia Machine")
    app.setOrganizationName("SynesthesiaMachine")
    
    # Set dark palette for Fusion style so popup menus (QComboBox dropdowns)
    # use dark backgrounds instead of the default white.
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(0x1a, 0x1b, 0x26))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0xc0, 0xca, 0xf5))
    palette.setColor(QPalette.ColorRole.Base, QColor(0x1a, 0x1b, 0x26))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(0x18, 0x19, 0x24))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0x1a, 0x1b, 0x26))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0xc0, 0xca, 0xf5))
    palette.setColor(QPalette.ColorRole.Text, QColor(0xc0, 0xca, 0xf5))
    palette.setColor(QPalette.ColorRole.Button, QColor(0x1a, 0x1b, 0x26))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0xc0, 0xca, 0xf5))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(0xff, 0xff, 0xff))
    palette.setColor(QPalette.ColorRole.Link, QColor(0x7a, 0xa2, 0xf7))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(0x64, 0x86, 0xff))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0xff, 0xff, 0xff))
    palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.Window, QColor(0x1a, 0x1b, 0x26))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window, QColor(0x1a, 0x1b, 0x26))
    palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Window, QColor(0x1a, 0x1b, 0x26))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()