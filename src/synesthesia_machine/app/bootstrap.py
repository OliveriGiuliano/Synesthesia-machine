"""Application composition root and console entry point."""

import logging
import sys
from collections.abc import Sequence
from multiprocessing import freeze_support

from PySide6.QtCore import QTimer

from synesthesia_machine.app.logging_setup import UI_LOGGER_NAME, configure_logging
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.app.shell import create_app_shell, process_client_factory


def _option_value(arguments: Sequence[str], option: str) -> str | None:
    try:
        index = arguments.index(option)
    except ValueError:
        return None
    if index + 1 >= len(arguments):
        raise ValueError(f"{option} requires a value")
    return arguments[index + 1]


def _qt_arguments(arguments: Sequence[str]) -> list[str]:
    values: list[str] = []
    skip_next = False
    for argument in arguments:
        if skip_next:
            skip_next = False
            continue
        if argument in {"--packaged-smoke-report", "--h264-video"}:
            skip_next = True
            continue
        if argument == "--smoke-test":
            continue
        values.append(argument)
    return values


def main(argv: Sequence[str] | None = None) -> int:
    """Start the minimal UI and return its Qt exit code."""

    freeze_support()
    arguments = list(sys.argv if argv is None else argv)
    packaged_smoke_report = _option_value(arguments, "--packaged-smoke-report")
    h264_video = _option_value(arguments, "--h264-video")
    if packaged_smoke_report is not None:
        from synesthesia_machine.app.release_smoke import run_packaged_smoke

        return run_packaged_smoke(packaged_smoke_report, h264_video=h264_video)

    smoke_test = "--smoke-test" in arguments
    qt_arguments = _qt_arguments(arguments)

    paths = ApplicationPaths.for_current_user()
    paths.ensure_exists()
    session = configure_logging(paths.logs)
    logger = logging.getLogger(UI_LOGGER_NAME)
    logger.info("Starting UI", extra={"session_id": session.session_id})

    shell = create_app_shell(
        argv=qt_arguments,
        paths=paths,
        engine_client_factory=process_client_factory(),
        offer_recovery=not smoke_test,
    )
    window = shell.window
    window.show()

    if smoke_test:
        QTimer.singleShot(100, shell.application.quit)

    try:
        exit_code = shell.application.exec()
    finally:
        try:
            shell.engine_client.close()
        except (RuntimeError, TimeoutError):
            logger.exception("Engine cleanup did not complete before UI shutdown")
    logger.info("UI stopped", extra={"exit_code": exit_code})
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
