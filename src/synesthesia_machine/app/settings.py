"""Application filesystem locations without Qt dependencies."""

from dataclasses import dataclass
from os import environ
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Per-user writable locations used by application infrastructure."""

    data: Path
    logs: Path
    recovery: Path

    @classmethod
    def for_current_user(cls) -> "ApplicationPaths":
        local_app_data = environ.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data is not None
            else Path.home() / "AppData" / "Local"
        )
        data = base / "SynesthesiaMachine"
        return cls(data=data, logs=data / "logs", recovery=data / "recovery")

    def ensure_exists(self) -> None:
        """Create application-owned directories idempotently."""

        self.logs.mkdir(parents=True, exist_ok=True)
        self.recovery.mkdir(parents=True, exist_ok=True)
