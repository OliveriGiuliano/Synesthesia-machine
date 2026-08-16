"""Lazy application-shell public API.

Keeping the package initializer import-free lets spawn-safe engine code use leaf utilities such as
structured logging without importing Qt or the UI composition root.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synesthesia_machine.app.application import MainWindow, create_application
    from synesthesia_machine.app.bootstrap import main

__all__ = ["MainWindow", "create_application", "main"]


def __getattr__(name: str) -> object:
    if name in {"MainWindow", "create_application"}:
        from synesthesia_machine.app.application import MainWindow, create_application

        return {"MainWindow": MainWindow, "create_application": create_application}[name]
    if name == "main":
        from synesthesia_machine.app.bootstrap import main

        return main
    raise AttributeError(name)
