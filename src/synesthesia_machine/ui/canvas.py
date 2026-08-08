"""Graphics scene/view synchronization, navigation, and cable gestures."""

from __future__ import annotations

import math
from uuid import UUID

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QInputDialog

from synesthesia_machine.ui.graphics import (
    ConnectionGraphicsItem,
    GroupGraphicsItem,
    NodeGraphicsItem,
    PortGraphicsItem,
    TemporaryConnectionGraphicsItem,
)
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import Theme

NODE_MIME_TYPE = "application/x-synesthesia-node-type"


class GraphScene(QGraphicsScene):
    connectionDroppedOnEmpty = Signal(object, object)

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
        self.group_items: dict[UUID, GroupGraphicsItem] = {}
        self._drag_port: PortGraphicsItem | None = None
        self._temporary: TemporaryConnectionGraphicsItem | None = None
        self.setSceneRect(-4000.0, -3000.0, 8000.0, 6000.0)
        session.changed.connect(self.sync_from_session)
        self.sync_from_session()

    def sync_from_session(self) -> None:
        selected_nodes = self.selected_node_ids()
        selected_connections = self.selected_connection_ids()
        selected_groups = self.selected_group_ids()
        self.clear()
        self.node_items.clear()
        self.connection_items.clear()
        self.group_items.clear()
        for group in self.session.document.groups:
            item = GroupGraphicsItem(group, self.theme)
            self.addItem(item)
            item.setSelected(group.id in selected_groups)
            self.group_items[group.id] = item
        view_model = self.session.view_model
        for node in view_model.nodes:
            item = NodeGraphicsItem(node, self.theme, self.session.set_parameter)
            self.addItem(item)
            item.setSelected(node.node_id in selected_nodes)
            self.node_items[node.node_id] = item
        for connection in view_model.connections:
            item = ConnectionGraphicsItem(connection, self.theme)
            self.addItem(item)
            item.setSelected(connection.connection_id in selected_connections)
            self.connection_items[connection.connection_id] = item
        self.update_connections()

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

    def update_connections(self) -> None:
        for item in self.connection_items.values():
            model = item.view_model
            source = self.port_item(model.source_node_id, model.source_port_id, True)
            destination = self.port_item(
                model.destination_node_id, model.destination_port_id, False
            )
            if source is not None and destination is not None:
                item.set_endpoints(source.scenePos(), destination.scenePos())

    def port_item(self, node_id: UUID, port_id: str, is_output: bool) -> PortGraphicsItem | None:
        node = self.node_items.get(node_id)
        return None if node is None else node.ports.get((port_id, is_output))

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

    def commit_node_move(self, origins: dict[UUID, tuple[float, float]]) -> None:
        current = {
            node_id: (self.node_items[node_id].pos().x(), self.node_items[node_id].pos().y())
            for node_id in origins
            if node_id in self.node_items
        }
        self.session.move_nodes(origins, current)

    def selected_group_positions(self) -> dict[UUID, tuple[float, float]]:
        return {
            group_id: (item.pos().x(), item.pos().y())
            for group_id, item in self.group_items.items()
            if item.isSelected()
        }

    def commit_group_move(self, origins: dict[UUID, tuple[float, float]]) -> None:
        current = {
            group_id: (self.group_items[group_id].pos().x(), self.group_items[group_id].pos().y())
            for group_id in origins
            if group_id in self.group_items
        }
        self.session.move_groups(origins, current)

    def edit_group(self, group_id: UUID) -> None:
        group = self.session.document.group(group_id)
        if group is None:
            return
        title, accepted = QInputDialog.getText(None, "Edit canvas item", "Title", text=group.title)
        if not accepted:
            return
        text, accepted = QInputDialog.getMultiLineText(
            None,
            "Edit canvas item",
            "Comment text",
            text=group.text,
        )
        if accepted:
            self.session.update_group(group_id, title=title, text=text)

    def delete_selection(self) -> None:
        self.session.delete_selection(
            self.selected_node_ids(), self.selected_connection_ids(), self.selected_group_ids()
        )

    def begin_connection_drag(self, port: PortGraphicsItem, position: QPointF) -> None:
        self._drag_port = port
        self._temporary = TemporaryConnectionGraphicsItem(self.theme)
        self.addItem(self._temporary)
        self._temporary.set_endpoints(port.scenePos(), position)
        for item in self.node_items.values():
            for target in item.ports.values():
                if target is port or target.view_model.is_output == port.view_model.is_output:
                    target.set_compatible(False)
                    continue
                source, destination = self._normalize_ports(port, target)
                target.set_compatible(
                    self.session.compatibility(
                        source.view_model.node_id,
                        source.view_model.port_id,
                        destination.view_model.node_id,
                        destination.view_model.port_id,
                    )
                )

    def update_connection_drag(self, position: QPointF) -> None:
        if self._drag_port is not None and self._temporary is not None:
            self._temporary.set_endpoints(self._drag_port.scenePos(), position)

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
        if self._temporary is not None:
            self.removeItem(self._temporary)
        for node in self.node_items.values():
            for port in node.ports.values():
                port.set_compatible(None)
        self._temporary = None
        self._drag_port = None

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
        spacing = self.theme.metrics.grid_size
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


class GraphView(QGraphicsView):
    requestSearch = Signal(object)

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

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        current = self.transform().m11()
        target = current * factor
        if self.theme.metrics.min_zoom <= target <= self.theme.metrics.max_zoom:
            self.scale(factor, factor)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
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

    def frame_all(self) -> None:
        rect = self.graph_scene.itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(
                rect.adjusted(-80.0, -80.0, 80.0, 80.0), Qt.AspectRatioMode.KeepAspectRatio
            )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME_TYPE):
            raw = event.mimeData().data(NODE_MIME_TYPE).data()
            payload = raw.tobytes() if isinstance(raw, memoryview) else raw
            type_id = payload.decode("utf-8")
            position = self.mapToScene(event.position().toPoint())
            self.graph_scene.session.add_node(type_id, (position.x(), position.y()))
            event.acceptProposedAction()
