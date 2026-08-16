"""Structured, rotating logging shared by UI and engine processes."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from uuid import uuid4

UI_LOGGER_NAME = "synesthesia_machine.ui"
ENGINE_LOGGER_NAME = "synesthesia_machine.engine"
_ROOT_LOGGER_NAME = "synesthesia_machine"


@dataclass(frozen=True, slots=True)
class LoggingSession:
    """Metadata describing one configured logging session."""

    session_id: str
    log_file: Path


class _ContextFilter(logging.Filter):
    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            record.session_id = self._session_id
        if not hasattr(record, "graph_revision"):
            record.graph_revision = None
        if not hasattr(record, "node_id"):
            record.node_id = None
        return True


class _JsonLineFormatter(logging.Formatter):
    def formatException(
        self,
        ei: (
            tuple[type[BaseException], BaseException, TracebackType | None]
            | tuple[None, None, None]
        ),
    ) -> str:
        return super().formatException(ei)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": getattr(record, "session_id", None),
            "process_id": record.process,
            "graph_revision": getattr(record, "graph_revision", None),
            "node_id": getattr(record, "node_id", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    log_directory: Path,
    *,
    session_id: str | None = None,
    level: int = logging.INFO,
    console: bool = True,
    process_name: str = "ui",
) -> LoggingSession:
    """Configure fresh rotating file and optional console handlers.

    Reconfiguration closes old handlers, which keeps tests and engine restarts leak-free.
    """
    active_session_id = session_id or uuid4().hex
    log_directory.mkdir(parents=True, exist_ok=True)
    if not process_name or not process_name.replace("-", "").isalnum():
        raise ValueError("process_name must contain only letters, numbers, and hyphens")
    log_file = log_directory / f"synesthesia-machine-{process_name}.jsonl"

    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(level)
    root_logger.propagate = False
    context_filter = _ContextFilter(active_session_id)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setLevel(level)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(_JsonLineFormatter())
    root_logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.addFilter(context_filter)
        console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root_logger.addHandler(console_handler)

    return LoggingSession(session_id=active_session_id, log_file=log_file)


__all__ = [
    "ENGINE_LOGGER_NAME",
    "UI_LOGGER_NAME",
    "LoggingSession",
    "configure_logging",
]
