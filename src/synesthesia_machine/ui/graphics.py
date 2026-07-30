"""Custom event-driven graphics objects for nodes, ports, and Bézier cables."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import cast
from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from synesthesia_machine.graph import LiteralValue
from synesthesia_machine.ui.parameter_editors import create_parameter_editor
from synesthesia_machine.ui.theme import Theme, port_color_name
from synesthesia_machine.ui.view_models import ConnectionViewModel, NodeViewModel, PortViewModel

type ParameterChangeHandler = Callable[[UUID, str, LiteralValue], None]


class PortGraphicsItem(QGraphicsObject):
    """Typed socket with shape and label cues in addition to colour."""

    def __init__(self, view_model: PortViewModel, theme: Theme, parent: QGraphicsItem) -> None:
        super().__init__(parent)
        self.view_model = view_model
        self.theme = theme
        self.compatible: bool | None = None
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        direction = "output" if view_model.is_output else "input"
        self.setToolTip(f"{view_model.label} — {view_model.type_name} {direction}")

    def boundingRect(self) -> QRectF:
        radius = self.theme.metrics.port_radius + 2.0
        return QRectF(-radius, -radius, radius * 2.0, radius * 2.0)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        color = self.theme.color(port_color_name(self.view_model.type_name))
        if self.compatible is False:
            color = self.theme.color("disabled")
        pen = QPen(self.theme.color("selection") if self.compatible else color)
        pen.setWidthF(2.0 if self.compatible else 1.3)
        painter.setPen(pen)
        painter.setBrush(QBrush(color if self.view_model.connected else self.theme.color("node")))
        radius = self.theme.metrics.port_radius
        if self.view_model.is_output:
            painter.drawRect(QRectF(-radius, -radius, radius * 2.0, radius * 2.0))
        elif self.view_model.is_parameter:
            path = QPainterPath(QPointF(0.0, -radius))
            path.lineTo(radius, 0.0)
            path.lineTo(0.0, radius)
            path.lineTo(-radius, 0.0)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.drawEllipse(QPointF(), radius, radius)

    def set_compatible(self, compatible: bool | None) -> None:
        self.compatible = compatible
        self.update()

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setScale(1.15)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setScale(1.0)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = cast("GraphSceneProtocol", self.scene())
        scene.begin_connection_drag(self, event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = cast("GraphSceneProtocol", self.scene())
        scene.update_connection_drag(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = cast("GraphSceneProtocol", self.scene())
        scene.end_connection_drag(event.scenePos())
        event.accept()


class NodeGraphicsItem(QGraphicsObject):
    """Movable projection of a NodeViewModel; it never mutates GraphDocument."""

    def __init__(
        self,
        view_model: NodeViewModel,
        theme: Theme,
        on_parameter_changed: ParameterChangeHandler,
    ) -> None:
        super().__init__()
        self.view_model = view_model
        self.theme = theme
        self._on_parameter_changed = on_parameter_changed
        self.ports: dict[tuple[str, bool], PortGraphicsItem] = {}
        self.parameter_editors: dict[str, QGraphicsProxyWidget] = {}
        self._drag_origin: dict[UUID, tuple[float, float]] = {}
        self._height = self._layout_height()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(*view_model.position)
        self.setToolTip(view_model.description)
        self._create_ports_and_editors()

    def _layout_height(self) -> float:
        if self.view_model.collapsed:
            return self.theme.metrics.header_height
        ordinary_inputs = sum(not port.is_parameter for port in self.view_model.inputs)
        rows = ordinary_inputs + len(self.view_model.parameters) + len(self.view_model.outputs)
        return self.theme.metrics.header_height + max(rows, 1) * self.theme.metrics.row_height + 8.0

    def _create_ports_and_editors(self) -> None:
        metrics = self.theme.metrics
        y = metrics.header_height + metrics.row_height / 2.0
        for port in (item for item in self.view_model.inputs if not item.is_parameter):
            child = PortGraphicsItem(port, self.theme, self)
            child.setPos(0.0, y)
            self.ports[(port.port_id, False)] = child
            y += metrics.row_height
        parameter_ports = {
            item.port_id: item for item in self.view_model.inputs if item.is_parameter
        }
        for parameter in self.view_model.parameters:
            port = parameter_ports.get(parameter.spec.id)
            if port is not None:
                child = PortGraphicsItem(port, self.theme, self)
                child.setPos(0.0, y)
                self.ports[(port.port_id, False)] = child
            callback = partial(
                self._on_parameter_changed,
                self.view_model.node_id,
                parameter.spec.id,
            )
            editor = create_parameter_editor(parameter, callback, compact=True)
            editor.setFixedWidth(118)
            proxy = QGraphicsProxyWidget(self)
            proxy.setWidget(editor)
            proxy.setPos(metrics.node_width - 128.0, y - metrics.row_height / 2.0 + 2.0)
            self.parameter_editors[parameter.spec.id] = proxy
            y += metrics.row_height
        for port in self.view_model.outputs:
            child = PortGraphicsItem(port, self.theme, self)
            child.setPos(metrics.node_width, y)
            self.ports[(port.port_id, True)] = child
            y += metrics.row_height

    def boundingRect(self) -> QRectF:
        margin = self.theme.metrics.port_radius + 3.0
        return QRectF(
            -margin,
            -margin,
            self.theme.metrics.node_width + margin * 2.0,
            self._height + margin * 2.0,
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        metrics = self.theme.metrics
        body = QRectF(0.0, 0.0, metrics.node_width, self._height)
        pen = QPen(
            self.theme.color("selection") if self.isSelected() else self.theme.color("border")
        )
        pen.setWidthF(2.2 if self.isSelected() else 1.0)
        painter.setPen(pen)
        painter.setBrush(QBrush(self.theme.color("node")))
        painter.drawRoundedRect(body, metrics.node_radius, metrics.node_radius)
        painter.setBrush(QBrush(self.theme.color("node_header")))
        painter.drawRoundedRect(
            QRectF(0.0, 0.0, metrics.node_width, metrics.header_height),
            metrics.node_radius,
            metrics.node_radius,
        )
        painter.setPen(self.theme.color("text"))
        painter.setFont(self.theme.title_font())
        painter.drawText(
            QRectF(12.0, 0.0, metrics.node_width - 38.0, metrics.header_height),
            Qt.AlignmentFlag.AlignVCenter,
            self.view_model.title,
        )
        if self.view_model.issues:
            painter.setBrush(QBrush(self.theme.color("error")))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QPointF(metrics.node_width - 17.0, metrics.header_height / 2.0), 6.0, 6.0
            )
        if self.view_model.collapsed:
            return
        painter.setFont(self.theme.body_font())
        y = metrics.header_height
        for port in (item for item in self.view_model.inputs if not item.is_parameter):
            self._draw_row(painter, y, port.label, port.type_name, False)
            y += metrics.row_height
        for parameter in self.view_model.parameters:
            detail = "Live input" if parameter.connected else ""
            self._draw_row(painter, y, parameter.spec.label, detail, False, parameter.connected)
            y += metrics.row_height
        for port in self.view_model.outputs:
            self._draw_row(painter, y, port.label, port.type_name, True)
            y += metrics.row_height

    def _draw_row(
        self,
        painter: QPainter,
        y: float,
        label: str,
        detail: str,
        right: bool,
        disabled: bool = False,
    ) -> None:
        metrics = self.theme.metrics
        painter.setPen(self.theme.color("disabled" if disabled else "text"))
        alignment = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft
        )
        painter.drawText(
            QRectF(13.0, y, metrics.node_width * 0.42, metrics.row_height), alignment, label
        )
        if not right and detail:
            painter.setPen(self.theme.color("muted_text" if not disabled else "disabled"))
            painter.drawText(
                QRectF(metrics.node_width * 0.42, y, metrics.node_width * 0.52, metrics.row_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                detail,
            )

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change is QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = cast("object | None", self.scene())
            if scene is not None:
                cast("GraphSceneProtocol", scene).update_connections()
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mousePressEvent(event)
        scene = cast("GraphSceneProtocol", self.scene())
        self._drag_origin = scene.selected_node_positions()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        scene = cast("GraphSceneProtocol", self.scene())
        scene.commit_node_move(self._drag_origin)
        self._drag_origin = {}


class ConnectionGraphicsItem(QGraphicsObject):
    def __init__(self, view_model: ConnectionViewModel, theme: Theme) -> None:
        super().__init__()
        self.view_model = view_model
        self.theme = theme
        self.path = QPainterPath()
        self.setZValue(-1.0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setToolTip(f"{view_model.source_port_id} → {view_model.destination_port_id}")

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self.prepareGeometryChange()
        self.path = bezier_path(start, end)
        self.update()

    def boundingRect(self) -> QRectF:
        return self.path.boundingRect().adjusted(-6.0, -6.0, 6.0, 6.0)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(self.theme.metrics.cable_hit_width)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        return stroker.createStroke(self.path)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        color_name = (
            "error" if self.view_model.issues else port_color_name(self.view_model.type_name)
        )
        pen = QPen(
            self.theme.color("selection") if self.isSelected() else self.theme.color(color_name)
        )
        pen.setWidthF(self.theme.metrics.cable_width + (1.0 if self.isSelected() else 0.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path)


class TemporaryConnectionGraphicsItem(QGraphicsObject):
    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.path = QPainterPath()
        self.setZValue(10.0)

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self.prepareGeometryChange()
        self.path = bezier_path(start, end)
        self.update()

    def boundingRect(self) -> QRectF:
        return self.path.boundingRect().adjusted(-5.0, -5.0, 5.0, 5.0)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        pen = QPen(self.theme.color("accent"))
        pen.setWidthF(self.theme.metrics.cable_width)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPath(self.path)


def bezier_path(start: QPointF, end: QPointF) -> QPainterPath:
    distance = max(abs(end.x() - start.x()) * 0.5, 50.0)
    path = QPainterPath(start)
    path.cubicTo(start + QPointF(distance, 0.0), end - QPointF(distance, 0.0), end)
    return path


class GraphSceneProtocol:
    """Structural methods used by items, avoiding a runtime circular import."""

    def begin_connection_drag(self, port: PortGraphicsItem, position: QPointF) -> None: ...
    def update_connection_drag(self, position: QPointF) -> None: ...
    def end_connection_drag(self, position: QPointF) -> None: ...
    def update_connections(self) -> None: ...
    def selected_node_positions(self) -> dict[UUID, tuple[float, float]]: ...
    def commit_node_move(self, origins: dict[UUID, tuple[float, float]]) -> None: ...
