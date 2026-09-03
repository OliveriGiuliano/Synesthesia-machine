"""Graphics scene/view synchronization, navigation, and cable gestures."""

from __future__ import annotations

import math
import re
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import (
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSignalBlocker,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QInputDialog

from synesthesia_machine.graph import (
    AlignMode,
    DistributionAxis,
    LayoutBox,
    align_boxes,
    distribute_boxes,
    tidy_boxes,
)
from synesthesia_machine.nodes.input.video import LOAD_VIDEO_TYPE_ID
from synesthesia_machine.ui.commands import PREVIEW_VISIBLE_KEY
from synesthesia_machine.ui.graphics import (
    ConnectionGraphicsItem,
    GroupGraphicsItem,
    NodeGraphicsItem,
    PortGraphicsItem,
    TemporaryConnectionGraphicsItem,
)
from synesthesia_machine.ui.parameter_editors import FilePathParameterEditor
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import Theme
from synesthesia_machine.ui.translations import tr

NODE_MIME_TYPE = "application/x-synesthesia-node-type"
LARGE_GRAPH_NODE_THRESHOLD = 200
ORGANIZE_NODE_PREVIEW_CLEARANCE = 24.0
ORGANIZE_MINIMUM_HORIZONTAL_GAP = 96.0
# The graph area spans +/- these extents (40000 x 30000 scene units, five times
# the legacy 8000 x 6000 rectangle) so large graphs can be arranged without
# running into the canvas edge.
SCENE_EXTENT_X = 20000.0
SCENE_EXTENT_Y = 15000.0
# File kinds the canvas accepts from the operating system: video files become
# Load Video nodes; saved graph files (".synmachine.json" reports its suffix as
# ".json") open into the editor.
VIDEO_FILE_SUFFIXES = frozenset(
    f".{extension}"
    for extension in re.findall(r"\*\.([A-Za-z0-9]+)", FilePathParameterEditor.VIDEO_FILTER)
)
GRAPH_FILE_SUFFIXES = frozenset({".json"})


class GraphScene(QGraphicsScene):
    connectionDroppedOnEmpty = Signal(object, object)
    connectionInspectRequested = Signal(object)

    def __init__(
        self,
        session: DocumentSession,
        theme: Theme,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.theme = theme
        self.node_items: dict[UUID, NodeGraphicsItem] = {}
        self.connection_items: dict[UUID, ConnectionGraphicsItem] = {}
        self._connection_preview_index: dict[tuple[UUID, str], list[ConnectionGraphicsItem]] = {}
        self.group_items: dict[UUID, GroupGraphicsItem] = {}
        self._drag_port: PortGraphicsItem | None = None
        self._temporary: TemporaryConnectionGraphicsItem | None = None
        self._large_graph_mode = False
        self._detail_visible = True
        self._node_heat_levels: dict[UUID, float] = {}
        self._selection_outline_rect = QRectF()
        self.grid_snap_enabled = False
        self.grid_spacing = theme.metrics.grid_size
        self.setSceneRect(
            -SCENE_EXTENT_X, -SCENE_EXTENT_Y, 2.0 * SCENE_EXTENT_X, 2.0 * SCENE_EXTENT_Y
        )
        session.changed.connect(self.sync_from_session)
        session.deviceCatalogueChanged.connect(self._rebuild_device_editors)
        self.selectionChanged.connect(self.update_selection_outline)
        self.sync_from_session()

    def sync_from_session(self) -> None:
        selected_nodes = self.selected_node_ids()
        selected_connections = self.selected_connection_ids()
        selected_groups = self.selected_group_ids()
        selection_blocker = QSignalBlocker(self)
        view_model = self.session.view_model
        large_graph_mode = len(view_model.nodes) >= LARGE_GRAPH_NODE_THRESHOLD
        force_node_rebuild = large_graph_mode != self._large_graph_mode
        self._large_graph_mode = large_graph_mode

        groups = {group.id: group for group in self.session.document.groups}
        for group_id, item in tuple(self.group_items.items()):
            if group_id not in groups or item.model != groups[group_id]:
                self.removeItem(item)
                del self.group_items[group_id]
        for group_id, group in groups.items():
            if group_id not in self.group_items:
                item = GroupGraphicsItem(group, self.theme)
                self.addItem(item)
                self._place_item_in_bounds(item)
                item.setSelected(group_id in selected_groups)
                self.group_items[group_id] = item

        connections = {item.connection_id: item for item in view_model.connections}
        for connection_id, item in tuple(self.connection_items.items()):
            if connection_id not in connections or item.view_model != connections[connection_id]:
                self.removeItem(item)
                del self.connection_items[connection_id]

        nodes = {node.node_id: node for node in view_model.nodes}
        for node_id, item in tuple(self.node_items.items()):
            if force_node_rebuild or node_id not in nodes or item.view_model != nodes[node_id]:
                self.removeItem(item)
                del self.node_items[node_id]
        for node in view_model.nodes:
            if node.node_id not in self.node_items:
                item = NodeGraphicsItem(
                    node,
                    self.theme,
                    self.session.set_parameter,
                    defer_parameter_editors=large_graph_mode,
                    device_choice_provider=self.session.device_parameter_choices,
                )
                item.set_detail_visible(self._detail_visible)
                item.set_heat_level(self._node_heat_levels.get(node.node_id))
                self.addItem(item)
                self._place_item_in_bounds(item)
                item.setSelected(node.node_id in selected_nodes)
                self.node_items[node.node_id] = item
        for connection in view_model.connections:
            if connection.connection_id not in self.connection_items:
                item = ConnectionGraphicsItem(connection, self.theme)
                item.togglePreviewRequested.connect(self._on_toggle_preview)
                self.addItem(item)
                item.setSelected(connection.connection_id in selected_connections)
                self.connection_items[connection.connection_id] = item
        # Preview updates arrive per source port at display cadence; index the
        # items so set_connection_*_preview is an O(matching items) lookup
        # instead of a scan over every connection in a large graph.
        self._rebuild_connection_preview_index()
        self.update_connections()
        self._sync_connection_preview_visibility()
        selection_changed = (
            selected_nodes != self.selected_node_ids()
            or selected_connections != self.selected_connection_ids()
            or selected_groups != self.selected_group_ids()
        )
        del selection_blocker
        if selection_changed:
            self.selectionChanged.emit()

    @Slot()
    def _rebuild_device_editors(self) -> None:
        selected_nodes = self.selected_node_ids()
        for item in tuple(self.node_items.values()):
            self.removeItem(item)
        self.node_items.clear()
        self.sync_from_session()
        for node_id in selected_nodes:
            if (item := self.node_items.get(node_id)) is not None:
                item.setSelected(True)

    @property
    def large_graph_mode(self) -> bool:
        return self._large_graph_mode

    def clamp_position_to_scene(
        self, x: float, y: float, width: float, height: float
    ) -> tuple[float, float]:
        """Clamp an item's top-left position so it stays inside the graph area.

        Items larger than the scene on an axis are pinned to that axis' edge so
        they remain reachable; everything else is clamped fully inside.
        """

        rect = self.sceneRect()
        return (
            self._clamped_axis(x, width, rect.left(), rect.width()),
            self._clamped_axis(y, height, rect.top(), rect.height()),
        )

    @staticmethod
    def _clamped_axis(value: float, size: float, lower: float, span: float) -> float:
        if size >= span:
            return lower
        return min(max(value, lower), lower + span - size)

    def clamp_new_node_anchor(self, position: QPointF) -> tuple[float, float]:
        """Keep a dropped node's top-left inside the graph area (fresh nodes always fit)."""

        metrics = self.theme.metrics
        minimum_height = metrics.header_height + metrics.row_height + 8.0
        return self.clamp_position_to_scene(
            position.x(), position.y(), float(metrics.node_width), minimum_height
        )

    def _place_item_in_bounds(self, item: QGraphicsItem) -> None:
        """Bring a freshly synced item inside the graph area (documents may contain
        positions beyond the bounds that clamping now enforces)."""

        x, y = item.pos().x(), item.pos().y()
        clamped = self.clamp_position_to_scene(
            x, y, item.boundingRect().width(), item.boundingRect().height()
        )
        if clamped != (x, y):
            item.setPos(*clamped)

    def set_detail_level(self, scale: float) -> None:
        detail_visible = scale >= 0.55
        if detail_visible == self._detail_visible:
            return
        self._detail_visible = detail_visible
        for item in self.node_items.values():
            item.set_detail_visible(detail_visible)

    def set_node_heatmap(self, levels: dict[UUID, float] | None) -> None:
        self._node_heat_levels = dict(levels or {})
        for node_id, item in self.node_items.items():
            item.set_heat_level(self._node_heat_levels.get(node_id))

    def _rebuild_connection_preview_index(self) -> None:
        index: dict[tuple[UUID, str], list[ConnectionGraphicsItem]] = {}
        for item in self.connection_items.values():
            view_model = item.view_model
            key = (view_model.source_node_id, view_model.source_port_id)
            index.setdefault(key, []).append(item)
        self._connection_preview_index = index

    def set_connection_value_preview(
        self, owner_node_id: UUID, source_port_id: str, text: str | None
    ) -> None:
        for item in self._connection_preview_index.get((owner_node_id, source_port_id), ()):
            if item.takes_value_pill():
                item.set_value_preview(text)

    def set_connection_image_preview(
        self, owner_node_id: UUID, source_port_id: str, image: QImage
    ) -> None:
        for item in self._connection_preview_index.get((owner_node_id, source_port_id), ()):
            if item.takes_image_pill():
                item.set_image_preview(image)

    def clear_connection_previews(self) -> None:
        """Remove runtime payloads retained by stable connection graphics items."""

        for item in self.connection_items.values():
            item.set_value_preview(None)
            item.set_image_preview(None)

    def _connection_preview_visible(self, connection_id: UUID) -> bool:
        connection = self.session.document.connection(connection_id)
        if connection is None:
            return True
        return bool(connection.ui_state.get(PREVIEW_VISIBLE_KEY, True))

    def _sync_connection_preview_visibility(self) -> None:
        for item in self.connection_items.values():
            item.set_preview_visible(
                self._connection_preview_visible(item.view_model.connection_id)
            )

    @Slot(object)
    def _on_toggle_preview(self, connection_id: UUID) -> None:
        self.session.set_connection_preview_visible(
            connection_id, not self._connection_preview_visible(connection_id)
        )

    def select_node_ids(self, node_ids: set[UUID]) -> None:
        self.clearSelection()
        for node_id in node_ids:
            item = self.node_items.get(node_id)
            if item is not None:
                item.setSelected(True)

    def select_group_ids(self, group_ids: set[UUID]) -> None:
        self.clearSelection()
        for group_id in group_ids:
            item = self.group_items.get(group_id)
            if item is not None:
                item.setSelected(True)

    def select_connection_ids(self, connection_ids: set[UUID]) -> None:
        self.clearSelection()
        for connection_id in connection_ids:
            item = self.connection_items.get(connection_id)
            if item is not None:
                item.setSelected(True)

    def update_connections(self) -> None:
        for item in self.connection_items.values():
            model = item.view_model
            source = self.port_item(model.source_node_id, model.source_port_id, True)
            destination = self.port_item(
                model.destination_node_id, model.destination_port_id, False
            )
            if source is not None and destination is not None:
                item.set_endpoints(source.scenePos(), destination.scenePos())
        self.update_selection_outline()

    @property
    def selection_outline_rect(self) -> QRectF:
        return QRectF(self._selection_outline_rect)

    @Slot()
    def update_selection_outline(self) -> None:
        bounds = QRectF()
        for item in self.node_items.values():
            if item.isSelected():
                bounds = bounds.united(item.body_scene_rect)
        margin = self.theme.metrics.selection_outline_margin
        updated = (
            bounds.adjusted(-margin, -margin, margin, margin) if not bounds.isEmpty() else bounds
        )
        dirty = self._selection_outline_rect.united(updated).adjusted(-3.0, -3.0, 3.0, 3.0)
        self._selection_outline_rect = updated
        if not dirty.isEmpty():
            self.invalidate(dirty, QGraphicsScene.SceneLayer.ForegroundLayer)

    def port_item(self, node_id: UUID, port_id: str, is_output: bool) -> PortGraphicsItem | None:
        node = self.node_items.get(node_id)
        return None if node is None else node.ports.get((port_id, is_output))

    def selected_node_items(self) -> list[NodeGraphicsItem]:
        return [self.node_items[node_id] for node_id in sorted(self.selected_node_ids(), key=str)]

    def selected_node_ids(self) -> set[UUID]:
        return {
            item.view_model.node_id
            for item in self.selectedItems()
            if isinstance(item, NodeGraphicsItem)
        }

    def selected_connection_ids(self) -> set[UUID]:
        return {
            item.view_model.connection_id
            for item in self.selectedItems()
            if isinstance(item, ConnectionGraphicsItem)
        }

    def selected_group_items(self) -> list[GroupGraphicsItem]:
        return [
            self.group_items[group_id] for group_id in sorted(self.selected_group_ids(), key=str)
        ]

    def selected_group_ids(self) -> set[UUID]:
        return {
            item.model.id for item in self.selectedItems() if isinstance(item, GroupGraphicsItem)
        }

    def selected_node_positions(self) -> dict[UUID, tuple[float, float]]:
        return {
            node_id: (item.pos().x(), item.pos().y())
            for node_id, item in self.node_items.items()
            if item.isSelected()
        }

    def inspect_connection(self, connection_id: UUID) -> None:
        self.connectionInspectRequested.emit(connection_id)

    def commit_node_move(self, origins: dict[UUID, tuple[float, float]]) -> None:
        # Nodes deleted between press and release drop out of the move entirely;
        # the old/new maps must cover the same (surviving) IDs.
        current: dict[UUID, tuple[float, float]] = {}
        surviving: dict[UUID, tuple[float, float]] = {}
        for node_id, origin in origins.items():
            item = self.node_items.get(node_id)
            if item is None:
                continue
            surviving[node_id] = origin
            x, y = self._snapped_position(item.pos().x(), item.pos().y())
            current[node_id] = self.clamp_position_to_scene(
                x, y, item.boundingRect().width(), item.boundingRect().height()
            )
        self.session.move_nodes(surviving, current)

    def selected_group_positions(self) -> dict[UUID, tuple[float, float]]:
        return {
            group_id: (item.pos().x(), item.pos().y())
            for group_id, item in self.group_items.items()
            if item.isSelected()
        }

    def commit_group_move(self, origins: dict[UUID, tuple[float, float]]) -> None:
        # Groups deleted between press and release drop out of the move entirely;
        # the old/new maps must cover the same (surviving) IDs.
        current: dict[UUID, tuple[float, float]] = {}
        surviving: dict[UUID, tuple[float, float]] = {}
        for group_id, origin in origins.items():
            item = self.group_items.get(group_id)
            if item is None:
                continue
            surviving[group_id] = origin
            x, y = self._snapped_position(item.pos().x(), item.pos().y())
            current[group_id] = self.clamp_position_to_scene(
                x, y, item.boundingRect().width(), item.boundingRect().height()
            )
        self.session.move_groups(surviving, current)

    def commit_group_resize(
        self,
        group_id: UUID,
        position: tuple[float, float],
        size: tuple[float, float],
    ) -> None:
        clamped = self.clamp_position_to_scene(
            position[0], position[1], float(size[0]), float(size[1])
        )
        self.session.update_group(group_id, position=clamped, size=size)

    def configure_grid_snap(self, *, enabled: bool, spacing: float) -> None:
        if not math.isfinite(spacing) or spacing <= 0.0:
            raise ValueError("Grid snap spacing must be finite and positive")
        self.grid_snap_enabled = enabled
        self.grid_spacing = spacing
        self.update()

    def _snapped_position(self, x: float, y: float) -> tuple[float, float]:
        if not self.grid_snap_enabled:
            return x, y
        return (
            round(x / self.grid_spacing) * self.grid_spacing,
            round(y / self.grid_spacing) * self.grid_spacing,
        )

    def edit_group(self, group_id: UUID) -> None:
        group = self.session.document.group(group_id)
        if group is None:
            return
        title, accepted = QInputDialog.getText(
            None, tr("Edit canvas item"), tr("Title"), text=group.title
        )
        if not accepted:
            return
        text, accepted = QInputDialog.getMultiLineText(
            None,
            tr("Edit canvas item"),
            tr("Comment text"),
            text=group.text,
        )
        if accepted:
            self.session.update_group(group_id, title=title, text=text)

    def delete_selection(self) -> None:
        self.session.delete_selection(
            self.selected_node_ids(), self.selected_connection_ids(), self.selected_group_ids()
        )

    def align_selection(self, mode: AlignMode) -> None:
        boxes = self._selected_layout_boxes()
        if len(boxes) < 2:
            return
        self._apply_layout(align_boxes(boxes, mode))

    def distribute_selection(self, axis: DistributionAxis) -> None:
        boxes = self._selected_layout_boxes()
        if len(boxes) < 3:
            return
        self._apply_layout(distribute_boxes(boxes, axis))

    def tidy_selection(self) -> None:
        boxes = self._selected_layout_boxes()
        if len(boxes) < 2:
            return
        self._apply_layout(tidy_boxes(boxes, self.session.document.connections))

    def organize_graph(self) -> None:
        """Arrange all nodes into layers with room for visible connection previews."""

        boxes = self._layout_boxes()
        if len(boxes) < 2:
            return
        preview_width = max(
            (item.preview_scene_rect.width() for item in self.connection_items.values()),
            default=0.0,
        )
        horizontal_gap = max(
            ORGANIZE_MINIMUM_HORIZONTAL_GAP,
            preview_width + ORGANIZE_NODE_PREVIEW_CLEARANCE,
        )
        self._apply_layout(
            tidy_boxes(
                boxes,
                self.session.document.connections,
                horizontal_gap=horizontal_gap,
            )
        )

    def _selected_layout_boxes(self) -> tuple[LayoutBox, ...]:
        selected = self.selected_node_ids()
        return tuple(box for box in self._layout_boxes() if box.node_id in selected)

    def _layout_boxes(self) -> tuple[LayoutBox, ...]:
        return tuple(
            LayoutBox(
                node_id,
                item.pos().x(),
                item.pos().y(),
                item.boundingRect().width(),
                item.boundingRect().height(),
            )
            for node_id, item in sorted(self.node_items.items(), key=lambda pair: str(pair[0]))
        )

    def _apply_layout(self, positions: dict[UUID, tuple[float, float]]) -> None:
        old_positions: dict[UUID, tuple[float, float]] = {}
        clamped: dict[UUID, tuple[float, float]] = {}
        for node_id, position in positions.items():
            node = self.session.document.node(node_id)
            if node is None:
                continue
            old_positions[node_id] = node.position
            item = self.node_items.get(node_id)
            if item is None:
                clamped[node_id] = position
                continue
            clamped[node_id] = self.clamp_position_to_scene(
                position[0], position[1], item.boundingRect().width(), item.boundingRect().height()
            )
        self.session.move_nodes(old_positions, clamped)

    def begin_connection_drag(self, port: PortGraphicsItem, position: QPointF) -> None:
        self.cancel_connection_drag()
        self._drag_port = port
        self._temporary = TemporaryConnectionGraphicsItem(self.theme)
        self.addItem(self._temporary)
        self._temporary.set_endpoints(port.scenePos(), position)
        candidates: list[tuple[PortGraphicsItem, tuple[UUID, str, UUID, str]]] = []
        for item in self.node_items.values():
            for target in item.ports.values():
                if target is port or target.view_model.is_output == port.view_model.is_output:
                    target.set_compatible(False)
                    continue
                source, destination = self._normalize_ports(port, target)
                endpoint = (
                    source.view_model.node_id,
                    source.view_model.port_id,
                    destination.view_model.node_id,
                    destination.view_model.port_id,
                )
                candidates.append((target, endpoint))
        compatibility = self.session.compatibilities(
            tuple(endpoint for _target, endpoint in candidates)
        )
        for target, endpoint in candidates:
            target.set_compatible(compatibility[endpoint])

    def update_connection_drag(self, position: QPointF) -> None:
        if self._drag_port is not None and self._temporary is not None:
            self._temporary.set_endpoints(self._drag_port.scenePos(), position)

    @property
    def connection_drag_active(self) -> bool:
        return self._drag_port is not None

    def cancel_connection_drag(self) -> None:
        if self._temporary is not None and self._temporary.scene() is self:
            self.removeItem(self._temporary)
        for node in self.node_items.values():
            for port in node.ports.values():
                port.set_compatible(None)
        self._temporary = None
        self._drag_port = None

    def end_connection_drag(self, position: QPointF) -> None:
        origin = self._drag_port
        target = next(
            (
                item
                for item in self.items(position)
                if isinstance(item, PortGraphicsItem) and item is not origin
            ),
            None,
        )
        connection: tuple[UUID, str, UUID, str] | None = None
        dropped_port = None
        if origin is not None and target is not None and target.compatible:
            source, destination = self._normalize_ports(origin, target)
            connection = (
                source.view_model.node_id,
                source.view_model.port_id,
                destination.view_model.node_id,
                destination.view_model.port_id,
            )
        elif origin is not None and target is None:
            dropped_port = origin.view_model

        # A successful command refreshes and clears the scene synchronously, so
        # release all gesture-owned graphics before mutating the document.
        self.cancel_connection_drag()

        if connection is not None:
            self.session.add_connection(*connection)
        elif dropped_port is not None:
            self.connectionDroppedOnEmpty.emit(dropped_port, position)

    @staticmethod
    def _normalize_ports(
        first: PortGraphicsItem, second: PortGraphicsItem
    ) -> tuple[PortGraphicsItem, PortGraphicsItem]:
        return (first, second) if first.view_model.is_output else (second, first)

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        rect = QRectF(rect)
        painter.fillRect(rect, QBrush(self.theme.color("canvas")))
        spacing = self.grid_spacing
        left = math.floor(rect.left() / spacing) * spacing
        top = math.floor(rect.top() / spacing) * spacing
        pen = QPen(self.theme.color("grid"))
        pen.setWidthF(0.0)
        painter.setPen(pen)
        x = left
        while x <= rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += spacing
        y = top
        while y <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += spacing

    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        super().drawForeground(painter, rect)
        if self._selection_outline_rect.isEmpty():
            return
        painter.save()
        pen = QPen(self.theme.color("selection_outline"))
        pen.setWidthF(self.theme.metrics.selection_outline_width)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = self.theme.metrics.node_radius + self.theme.metrics.selection_outline_margin
        painter.drawRoundedRect(self._selection_outline_rect, radius, radius)
        painter.restore()


class GraphView(QGraphicsView):
    requestSearch = Signal(object)
    openGraphFileRequested = Signal(Path)

    def __init__(self, scene: GraphScene, theme: Theme) -> None:
        super().__init__(scene)
        self.graph_scene = scene
        self.theme = theme
        self._panning = False
        self._space_pressed = False
        self._space_pan_used = False
        self._last_pan = QPoint()
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAcceptDrops(True)
        self.setBackgroundBrush(QBrush(theme.color("canvas")))
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)

    def wheelEvent(self, event: QWheelEvent) -> None:
        vertical_delta = event.angleDelta().y()
        if vertical_delta == 0:
            super().wheelEvent(event)
            return
        factor = 1.15 if vertical_delta > 0 else 1.0 / 1.15
        current = self.transform().m11()
        target = current * factor
        if self.theme.metrics.min_zoom <= target <= self.theme.metrics.max_zoom:
            self.scale(factor, factor)
            self.graph_scene.set_detail_level(self.transform().m11())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.graph_scene.connection_drag_active:
            self.graph_scene.cancel_connection_drag()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self._space_pan_used = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.graph_scene.delete_selection()
            event.accept()
            return
        if event.key() == Qt.Key.Key_F:
            self.frame_selection()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Home:
            self.frame_all()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if not self._space_pan_used:
                self.requestSearch.emit(self.mapToScene(self.viewport().rect().center()))
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.RightButton and self.graph_scene.connection_drag_active:
            self.graph_scene.cancel_connection_drag()
            event.accept()
            return
        if event.button() is Qt.MouseButton.MiddleButton or (
            event.button() is Qt.MouseButton.LeftButton and self._space_pressed
        ):
            self._panning = True
            self._space_pan_used = self._space_pressed
            self._last_pan = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            current = event.position().toPoint()
            delta = current - self._last_pan
            self._last_pan = current
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            self._panning = False
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self._space_pressed else Qt.CursorShape.ArrowCursor
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() is Qt.MouseButton.LeftButton
            and self.itemAt(event.position().toPoint()) is None
        ):
            self.requestSearch.emit(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self.itemAt(event.pos()) is None:
            self.requestSearch.emit(self.mapToScene(event.pos()))
            event.accept()
            return
        super().contextMenuEvent(event)

    def frame_selection(self) -> None:
        selected = self.graph_scene.selectedItems()
        if selected:
            rect = QRectF()
            for item in selected:
                rect = rect.united(item.sceneBoundingRect())
            self.fitInView(
                rect.adjusted(-60.0, -60.0, 60.0, 60.0), Qt.AspectRatioMode.KeepAspectRatio
            )
            self.graph_scene.set_detail_level(self.transform().m11())

    def frame_all(self) -> None:
        rect = self.graph_scene.itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(
                rect.adjusted(-80.0, -80.0, 80.0, 80.0), Qt.AspectRatioMode.KeepAspectRatio
            )
            self.graph_scene.set_detail_level(self.transform().m11())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME_TYPE) or self._dropped_file_kind(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME_TYPE) or self._dropped_file_kind(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        mime_data = event.mimeData()
        if mime_data.hasFormat(NODE_MIME_TYPE):
            raw = mime_data.data(NODE_MIME_TYPE).data()
            payload = raw.tobytes() if isinstance(raw, memoryview) else raw
            type_id = payload.decode("utf-8")
            position = self.mapToScene(event.position().toPoint())
            self.graph_scene.session.add_node(
                type_id, self.graph_scene.clamp_new_node_anchor(position)
            )
            event.acceptProposedAction()
            return
        kind = self._dropped_file_kind(mime_data)
        if kind is None:
            return
        urls = mime_data.urls()
        if not urls or not urls[0].isLocalFile():
            return
        path = Path(urls[0].toLocalFile())
        position = self.mapToScene(event.position().toPoint())
        if kind == "video":
            # A dropped video becomes a Load Video node whose path is already set,
            # as one undo step.
            self.graph_scene.session.add_node(
                LOAD_VIDEO_TYPE_ID,
                self.graph_scene.clamp_new_node_anchor(position),
                parameters={"file_path": path.as_posix()},
            )
        else:
            self.openGraphFileRequested.emit(path)
        event.acceptProposedAction()

    @staticmethod
    def _dropped_file_kind(mime_data: QMimeData) -> str | None:
        """Classify the first dropped local file as a video or a saved graph."""

        urls = mime_data.urls()
        if not urls or not urls[0].isLocalFile():
            return None
        suffix = Path(urls[0].toLocalFile()).suffix.lower()
        if suffix in VIDEO_FILE_SUFFIXES:
            return "video"
        if suffix in GRAPH_FILE_SUFFIXES:
            return "graph"
        return None
