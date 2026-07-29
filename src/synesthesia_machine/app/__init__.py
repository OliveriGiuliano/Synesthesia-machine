"""Application shell public API.

This package may depend on Qt. Engine, graph, and node algorithm packages must not import it.
"""

from synesthesia_machine.app.application import MainWindow, create_application
from synesthesia_machine.app.bootstrap import main

__all__ = ["MainWindow", "create_application", "main"]
