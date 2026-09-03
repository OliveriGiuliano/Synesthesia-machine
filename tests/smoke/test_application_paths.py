"""Platform-aware application data-root resolution (XDG on Linux, LOCALAPPDATA on Windows)."""

import os
from pathlib import Path

import pytest

from synesthesia_machine.app.settings import ApplicationPaths, data_base


def test_linux_absolute_xdg_data_home_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("Linux data-root behaviour")
    sandbox = Path("/tmp") / "synmachine-xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(sandbox))
    assert data_base() == str(sandbox)
    paths = ApplicationPaths.for_current_user()
    assert paths.data == Path(sandbox) / "SynesthesiaMachine"
    assert paths.logs == Path(sandbox) / "SynesthesiaMachine" / "logs"


@pytest.mark.parametrize("value", ["", "relative/data"])
def test_linux_empty_or_relative_xdg_data_home_falls_back(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    if os.name != "posix":
        pytest.skip("Linux data-root behaviour")
    # The XDG spec requires a relative (or empty) $XDG_DATA_HOME to be
    # interpreted relative to $HOME.
    monkeypatch.setenv("XDG_DATA_HOME", value)
    assert data_base() == str(Path.home() / ".local" / "share")


def test_linux_default_data_root(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("Linux data-root behaviour")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert data_base() == str(Path.home() / ".local" / "share")
    assert ApplicationPaths.for_current_user().data == (
        Path.home() / ".local" / "share" / "SynesthesiaMachine"
    )


def test_windows_localappdata_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        pytest.skip("Windows data-root behaviour")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    assert data_base() == r"C:\Users\tester\AppData\Local"
    assert str(ApplicationPaths.for_current_user().data) == (
        r"C:\Users\tester\AppData\Local\SynesthesiaMachine"
    )
