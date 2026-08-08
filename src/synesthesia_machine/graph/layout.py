"""Deterministic Qt-free alignment, distribution, and graph-aware tidy algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from synesthesia_machine.graph.model import ConnectionModel


class AlignMode(StrEnum):
    LEFT = "LEFT"
    HORIZONTAL_CENTER = "HORIZONTAL_CENTER"
    RIGHT = "RIGHT"
    TOP = "TOP"
    VERTICAL_CENTER = "VERTICAL_CENTER"
    BOTTOM = "BOTTOM"


class DistributionAxis(StrEnum):
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"


@dataclass(frozen=True, slots=True)
class LayoutBox:
    node_id: UUID
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


def align_boxes(boxes: tuple[LayoutBox, ...], mode: AlignMode) -> dict[UUID, tuple[float, float]]:
    if len(boxes) < 2:
        raise ValueError("Alignment requires at least two nodes")
    if mode is AlignMode.LEFT:
        target = min(box.x for box in boxes)
        return {box.node_id: (target, box.y) for box in boxes}
    if mode is AlignMode.RIGHT:
        target = max(box.right for box in boxes)
        return {box.node_id: (target - box.width, box.y) for box in boxes}
    if mode is AlignMode.HORIZONTAL_CENTER:
        target = sum(box.center_x for box in boxes) / len(boxes)
        return {box.node_id: (target - box.width / 2.0, box.y) for box in boxes}
    if mode is AlignMode.TOP:
        target = min(box.y for box in boxes)
        return {box.node_id: (box.x, target) for box in boxes}
    if mode is AlignMode.BOTTOM:
        target = max(box.bottom for box in boxes)
        return {box.node_id: (box.x, target - box.height) for box in boxes}
    if mode is AlignMode.VERTICAL_CENTER:
        target = sum(box.center_y for box in boxes) / len(boxes)
        return {box.node_id: (box.x, target - box.height / 2.0) for box in boxes}
    raise ValueError(f"Unknown alignment mode: {mode!r}")


def distribute_boxes(
    boxes: tuple[LayoutBox, ...], axis: DistributionAxis
) -> dict[UUID, tuple[float, float]]:
    if len(boxes) < 3:
        raise ValueError("Distribution requires at least three nodes")
    if axis is DistributionAxis.HORIZONTAL:
        ordered = sorted(boxes, key=lambda box: (box.x, str(box.node_id)))
        start = ordered[0].x
        end = ordered[-1].right
        gap = (end - start - sum(box.width for box in ordered)) / (len(ordered) - 1)
        cursor = start
        result: dict[UUID, tuple[float, float]] = {}
        for box in ordered:
            result[box.node_id] = (cursor, box.y)
            cursor += box.width + gap
        return result
    if axis is DistributionAxis.VERTICAL:
        ordered = sorted(boxes, key=lambda box: (box.y, str(box.node_id)))
        start = ordered[0].y
        end = ordered[-1].bottom
        gap = (end - start - sum(box.height for box in ordered)) / (len(ordered) - 1)
        cursor = start
        result = {}
        for box in ordered:
            result[box.node_id] = (box.x, cursor)
            cursor += box.height + gap
        return result
    raise ValueError(f"Unknown distribution axis: {axis!r}")


def tidy_boxes(
    boxes: tuple[LayoutBox, ...],
    connections: tuple[ConnectionModel, ...],
    *,
    horizontal_gap: float = 96.0,
    vertical_gap: float = 48.0,
) -> dict[UUID, tuple[float, float]]:
    """Lay selected DAG nodes into deterministic left-to-right longest-path layers."""

    if len(boxes) < 2:
        raise ValueError("Tidy selection requires at least two nodes")
    by_id = {box.node_id: box for box in boxes}
    selected = set(by_id)
    outgoing: dict[UUID, set[UUID]] = {node_id: set() for node_id in selected}
    indegree = {node_id: 0 for node_id in selected}
    for connection in connections:
        source = connection.source_node_id
        destination = connection.destination_node_id
        if source not in selected or destination not in selected or destination in outgoing[source]:
            continue
        outgoing[source].add(destination)
        indegree[destination] += 1

    levels = {node_id: 0 for node_id in selected}
    ready = sorted((node_id for node_id, degree in indegree.items() if degree == 0), key=str)
    visited: list[UUID] = []
    while ready:
        node_id = ready.pop(0)
        visited.append(node_id)
        for destination in sorted(outgoing[node_id], key=str):
            levels[destination] = max(levels[destination], levels[node_id] + 1)
            indegree[destination] -= 1
            if indegree[destination] == 0:
                ready.append(destination)
                ready.sort(key=str)
    if len(visited) != len(selected):
        raise ValueError("Tidy selection requires an acyclic selected subgraph")

    origin_x = min(box.x for box in boxes)
    origin_y = min(box.y for box in boxes)
    maximum_width = max(box.width for box in boxes)
    result: dict[UUID, tuple[float, float]] = {}
    for level in range(max(levels.values()) + 1):
        layer = sorted(
            (by_id[node_id] for node_id, item_level in levels.items() if item_level == level),
            key=lambda box: (box.y, box.x, str(box.node_id)),
        )
        cursor_y = origin_y
        for box in layer:
            result[box.node_id] = (origin_x + level * (maximum_width + horizontal_gap), cursor_y)
            cursor_y += box.height + vertical_gap
    return result
