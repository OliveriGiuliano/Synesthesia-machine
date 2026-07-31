"""Application composition root and console entry point."""

import logging
import sys
from collections.abc import Sequence

from PySide6.QtCore import QTimer

from synesthesia_machine.app.application import MainWindow, create_application
from synesthesia_machine.app.logging_setup import UI_LOGGER_NAME, configure_logging
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.runtime import InProcessEngineClient


def main(argv: Sequence[str] | None = None) -> int:
    """Start the minimal UI and return its Qt exit code."""

    arguments = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in arguments
    qt_arguments = [argument for argument in arguments if argument != "--smoke-test"]

    paths = ApplicationPaths.for_current_user()
    paths.ensure_exists()
    session = configure_logging(paths.logs)
    logger = logging.getLogger(UI_LOGGER_NAME)
    logger.info("Starting UI", extra={"session_id": session.session_id})

    application = create_application(qt_arguments)
    registry = create_application_registry()
    engine_client = InProcessEngineClient(registry)
    window = MainWindow(registry, paths, engine_client, offer_recovery=not smoke_test)
    window.show()

    if smoke_test:
        QTimer.singleShot(100, application.quit)

    try:
        exit_code = application.exec()
    finally:
        engine_client.close()
    logger.info("UI stopped", extra={"exit_code": exit_code})
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
