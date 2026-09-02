"""Application filesystem locations without Qt dependencies."""

import os
from dataclasses import dataclass
from os import environ
from pathlib import Path


def data_base() -> str:
    """Per-user data root for the application's own runtime state.

    Windows keeps the historical ``%LOCALAPPDATA%`` layout; Linux honours the
    XDG Base Directory Specification (``$XDG_DATA_HOME``, defaulting to
    ``$HOME/.local/share``). The result is a plain string so host-logic tests
    never touch platform-specific ``pathlib`` constructors.
    """

    if os.name == "nt":
        local_app_data = environ.get("LOCALAPPDATA")
        if local_app_data is not None:
            return local_app_data
        return str(Path.home() / "AppData" / "Local")
    xdg_data_home = environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return xdg_data_home
    return str(Path.home() / ".local" / "share")


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Per-user writable locations used by application infrastructure."""

    data: Path
    logs: Path
    recovery: Path

    @classmethod
    def for_current_user(cls) -> "ApplicationPaths":
        data = Path(data_base()) / "SynesthesiaMachine"
        return cls(data=data, logs=data / "logs", recovery=data / "recovery")

    def ensure_exists(self) -> None:
        """Create application-owned directories idempotently."""

        self.logs.mkdir(parents=True, exist_ok=True)
        self.recovery.mkdir(parents=True, exist_ok=True)
