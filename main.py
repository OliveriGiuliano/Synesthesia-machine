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
from synesthesia_machine.gui.main_window import MainWindow


def main():
    """Launch the Synesthesia Machine application."""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Synesthesia Machine")
    app.setOrganizationName("SynesthesiaMachine")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()