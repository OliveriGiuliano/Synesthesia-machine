"""One app-shell seam shared by the composition root and the tool harnesses.

Assembling the Qt shell (QApplication + node registry + engine client +
MainWindow) used to be re-implemented in five places, each knowing the
``MainWindow`` constructor and reaching into window internals. This module is
the single owner of that assembly: :func:`create_app_shell` builds the shell
from an engine-client factory, and the harnesses drive the window through its
public observation API instead of private attributes.

The engine client factory is the one variable: production passes the process
client, diagnostic harnesses pass a tuned process client or the in-process
client. Everything else (registry, window construction) is fixed here, so the
``MainWindow`` constructor is known in exactly one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from synesthesia_machine.app.application import create_application
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import EngineClient
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.runtime import InProcessEngineClient, ProcessEngineClient
from synesthesia_machine.ui.main_window import MainWindow

__all__ = [
    "AppShell",
    "EngineClientFactory",
    "create_app_shell",
    "in_process_client_factory",
    "process_client_factory",
]


class EngineClientFactory(Protocol):
    """Build the engine client for a shell given its registry and paths."""

    def __call__(self, *, registry: NodeRegistry, paths: ApplicationPaths) -> EngineClient: ...


@dataclass(frozen=True, slots=True)
class AppShell:
    """The assembled Qt application, its main window, and the engine client.

    The window wraps the client in its own engine session; the shell keeps the
    client handle so harnesses can drive and tear it down directly.
    """

    application: QApplication
    window: MainWindow
    engine_client: EngineClient


def create_app_shell(
    *,
    argv: Sequence[str] | None = None,
    paths: ApplicationPaths,
    engine_client_factory: EngineClientFactory,
    settings: QSettings | None = None,
    offer_recovery: bool = True,
) -> AppShell:
    """Create the QApplication, node registry, engine client, and MainWindow.

    ``paths`` must already be materialised (``ensure_exists``); production uses
    :meth:`ApplicationPaths.for_current_user` while harnesses build a temp-root
    layout. ``settings`` defaults to the per-user QSettings inside the window.
    """

    application = create_application(argv)
    registry = create_application_registry()
    engine_client = engine_client_factory(registry=registry, paths=paths)
    window = MainWindow(
        registry,
        paths,
        engine_client,
        settings=settings,
        offer_recovery=offer_recovery,
    )
    return AppShell(
        application=application,
        window=window,
        engine_client=engine_client,
    )


def process_client_factory(
    *,
    startup_timeout_s: float | None = None,
    request_timeout_s: float | None = None,
    activation_timeout_s: float | None = None,
    heartbeat_timeout_s: float | None = None,
    close_timeout_s: float | None = None,
) -> EngineClientFactory:
    """A factory building a spawned-engine client for the shell.

    Timeouts left as ``None`` fall back to ``ProcessEngineClient`` defaults, so
    the production shell (no overrides) and the tuned diagnostic harnesses share
    one code path. The crash log is always written under the shell's log dir.
    """

    def factory(*, registry: NodeRegistry, paths: ApplicationPaths) -> ProcessEngineClient:
        del registry
        kwargs: dict[str, object] = {"crash_log_path": paths.logs / "engine-crash.log"}
        if startup_timeout_s is not None:
            kwargs["startup_timeout_s"] = startup_timeout_s
        if request_timeout_s is not None:
            kwargs["request_timeout_s"] = request_timeout_s
        if activation_timeout_s is not None:
            kwargs["activation_timeout_s"] = activation_timeout_s
        if heartbeat_timeout_s is not None:
            kwargs["heartbeat_timeout_s"] = heartbeat_timeout_s
        if close_timeout_s is not None:
            kwargs["close_timeout_s"] = close_timeout_s
        return ProcessEngineClient(**kwargs)  # type: ignore[arg-type]

    return factory


def in_process_client_factory() -> EngineClientFactory:
    """A factory building an in-process engine client (diagnostic-only).

    The engine runs in the same process as the UI, so a crash takes the shell
    down with it; harnesses using this are explicitly diagnostic, not a
    substitute for the spawned-engine path.
    """

    def factory(*, registry: NodeRegistry, paths: ApplicationPaths) -> InProcessEngineClient:
        del paths
        return InProcessEngineClient(registry)

    return factory
