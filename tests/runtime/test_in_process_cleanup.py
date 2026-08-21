"""Adversarial cleanup coverage for the child-owned in-process engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest

from synesthesia_machine.contracts import EngineConnectionState
from synesthesia_machine.nodes import NodeRegistry
from synesthesia_machine.runtime.engine_facade import EngineFacade
from synesthesia_machine.runtime.in_process_engine import (
    InProcessEngineClient,
    LatestFrameGraphWorker,
    SourceController,
)
from synesthesia_machine.runtime.previews import PreviewBroker


@dataclass
class _Closer:
    failure: Exception | None = None
    close_count: int = 0

    def close(self) -> None:
        self.close_count += 1
        if self.failure is not None:
            raise self.failure

    def clear(self) -> None:
        self.close()


def test_close_attempts_every_resource_after_failure_and_remains_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = InProcessEngineClient(NodeRegistry(()))
    failing_source = _Closer(OSError("synthetic source close failure"))
    healthy_source = _Closer()
    worker = _Closer()
    previews = _Closer()
    facade = _Closer()
    monkeypatch.setattr(
        client,
        "_sources",
        {
            UUID(int=1): cast(SourceController, failing_source),
            UUID(int=2): cast(SourceController, healthy_source),
        },
    )
    monkeypatch.setattr(client, "_worker", cast(LatestFrameGraphWorker, worker))
    monkeypatch.setattr(client, "_preview_broker", cast(PreviewBroker, previews))
    monkeypatch.setattr(client, "_facade", cast(EngineFacade, facade))

    with pytest.raises(OSError, match="synthetic source close failure"):
        client.close()

    assert failing_source.close_count == 1
    assert healthy_source.close_count == 1
    assert worker.close_count == 1
    assert previews.close_count == 1
    assert facade.close_count == 1
    assert client.status().connection_state is EngineConnectionState.CLOSED

    client.close()
    assert failing_source.close_count == 1
    assert healthy_source.close_count == 1
    assert worker.close_count == 1
    assert previews.close_count == 1
    assert facade.close_count == 1
