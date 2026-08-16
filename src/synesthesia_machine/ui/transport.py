"""Deterministic source-scoped transport target selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.ui.translations import tr, trf


@dataclass(frozen=True, slots=True)
class TransportTargetResolution:
    target: UUID | None
    message: str


def resolve_transport_target(
    source_ids: Iterable[UUID],
    selected_node_ids: Iterable[UUID],
) -> TransportTargetResolution:
    sources = tuple(sorted(set(source_ids), key=str))
    selected = frozenset(selected_node_ids)
    selected_sources = tuple(source_id for source_id in sources if source_id in selected)
    if len(selected_sources) == 1:
        target = selected_sources[0]
        return TransportTargetResolution(
            target, trf("Targeting selected source {source}", source=str(target)[:8])
        )
    if len(selected_sources) > 1:
        return TransportTargetResolution(None, tr("Select exactly one source for transport"))
    if len(sources) == 1:
        target = sources[0]
        return TransportTargetResolution(
            target, trf("Targeting sole source {source}", source=str(target)[:8])
        )
    if not sources:
        return TransportTargetResolution(None, tr("Add a source node before using transport"))
    return TransportTargetResolution(None, tr("Select one source node for transport"))


__all__ = ["TransportTargetResolution", "resolve_transport_target"]
