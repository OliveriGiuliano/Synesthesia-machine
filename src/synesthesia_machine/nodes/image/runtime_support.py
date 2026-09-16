"""Shared stateless base runtime for image node families."""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.nodes import ResetReason


class StatelessImageRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return
