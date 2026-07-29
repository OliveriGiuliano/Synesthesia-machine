"""Smoke checks for structured rotating log creation."""

import json
import logging
from pathlib import Path

from synesthesia_machine.app.logging_setup import UI_LOGGER_NAME, configure_logging


def test_configure_logging_creates_structured_file(tmp_path: Path) -> None:
    session = configure_logging(tmp_path, session_id="test-session", console=False)
    logging.getLogger(UI_LOGGER_NAME).info("bootstrap ready")
    logging.shutdown()

    payload = json.loads(session.log_file.read_text(encoding="utf-8"))
    assert payload["message"] == "bootstrap ready"
    assert payload["session_id"] == "test-session"
    assert payload["logger"] == UI_LOGGER_NAME
    assert isinstance(payload["process_id"], int)
