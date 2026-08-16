"""Compatibility imports for the shared structured logging helpers."""

from synesthesia_machine.diagnostics.logging_setup import (
    ENGINE_LOGGER_NAME,
    UI_LOGGER_NAME,
    LoggingSession,
    configure_logging,
)

__all__ = [
    "ENGINE_LOGGER_NAME",
    "UI_LOGGER_NAME",
    "LoggingSession",
    "configure_logging",
]
