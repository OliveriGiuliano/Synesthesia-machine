"""Custom event-driven graphics objects for nodes, ports, and Bézier cables."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import cast
from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from synesthesia_machine.graph import (
    GroupKind,
    GroupModel,
    LiteralValue,
    ValidationIssue,
    ValidationSeverity,
)
from synesthesia_machine.ui.parameter_editors import create_parameter_editor
from synesthesia_machine.ui.theme import Theme, port_color_name
from synesthesia_machine.ui.view_models import (
    ConnectionViewModel,
    NodeViewModel,
    ParameterViewModel,
    PortViewModel,
)

type ParameterChangeHandler = Callable[[UUID, str, LiteralValue], None]


def _diagnostic_tooltip(description: str, issues: tuple[ValidationIssue, ...]) -> str:
    if not issues:
        return description
    details = "\n".join(f"{issue.severity} · {issue.message} ({issue.code})" for issue in issues)
    return f"{description}\n\n{details}"


class GroupGraphicsItem(QGraphicsObject):
    """Movable persisted group/comment projection rendered behind graph nodes."""

    def __init__(self, model: GroupModel, theme: Theme) -> None:
        super().__init__()
        self.model = model
        self.theme = theme
        self._drag_origin: dict[UUID, tuple[float, float]] = {}
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.setPos(*model.position)
        self.setZValue(-3.0 if model.kind is GroupKind.GROUP else -0.75)
        self.setToolTip(model.text or model.title)

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, self.model.size[0], self.model.size[1])

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        body = self.boundingRect()
        fill = QColor(self.model.color)
        fill.setAlpha(42 if self.model.kind is GroupKind.GROUP else 78)
        border = self.theme.color("selection") if self.isSelected() else QColor(self.model.color)
        pen = QPen(border)
        pen.setWidthF(2.2 if self.isSelected() else 1.2)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(body, 8.0, 8.0)
        painter.setPen(self.theme.color("text"))
        painter.setFont(self.theme.title_font())
        painter.drawText(
            QRectF(12.0, 6.0, max(0.0, body.width() - 24.0), 26.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.model.title,
        )
        if self.model.text:
            painter.setFont(self.theme.body_font())
            painter.setPen(self.theme.color("muted_text"))
            painter.drawText(
                QRectF(12.0, 36.0, max(0.0, body.width() - 24.0), max(0.0, body.height() - 48.0)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                self.model.text,
            )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mousePressEvent(event)
        scene = cast("GraphSceneProtocol", self.scene())
        self._drag_origin = scene.selected_group_positions()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        scene = cast("GraphSceneProtocol", self.scene())
        scene.commit_group_move(self._drag_origin)
        self._drag_origin = {}

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        cast("GraphSceneProtocol", self.scene()).edit_group(self.model.id)
        event.accept()


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
        *,
        defer_parameter_editors: bool = False,
    ) -> None:
        super().__init__()
        self.view_model = view_model
        self.theme = theme
        self._on_parameter_changed = on_parameter_changed
        self.ports: dict[tuple[str, bool], PortGraphicsItem] = {}
        self.parameter_editors: dict[str, QGraphicsProxyWidget] = {}
        self._parameter_rows: dict[str, float] = {}
        self._defer_parameter_editors = defer_parameter_editors
        self._detail_visible = True
        self._drag_origin: dict[UUID, tuple[float, float]] = {}
        self._height = self._layout_height()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(*view_model.position)
        self.setToolTip(_diagnostic_tooltip(view_model.description, view_model.issues))
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
            self._parameter_rows[parameter.spec.id] = y
            if not self._defer_parameter_editors:
                self._create_parameter_editor(parameter, y)
            y += metrics.row_height
        for port in self.view_model.outputs:
            child = PortGraphicsItem(port, self.theme, self)
            child.setPos(metrics.node_width, y)
            self.ports[(port.port_id, True)] = child
            y += metrics.row_height

    def _create_parameter_editor(self, parameter: ParameterViewModel, y: float) -> None:
        callback = partial(
            self._on_parameter_changed,
            self.view_model.node_id,
            parameter.spec.id,
        )
        editor = create_parameter_editor(parameter, callback, compact=True)
        editor.setFixedWidth(118)
        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(editor)
        proxy.setPos(
            self.theme.metrics.node_width - 128.0,
            y - self.theme.metrics.row_height / 2.0 + 2.0,
        )
        proxy.setVisible(self._detail_visible)
        self.parameter_editors[parameter.spec.id] = proxy

    def ensure_parameter_editors(self) -> None:
        for parameter in self.view_model.parameters:
            if parameter.spec.id not in self.parameter_editors:
                self._create_parameter_editor(parameter, self._parameter_rows[parameter.spec.id])

    def set_detail_visible(self, visible: bool) -> None:
        if self._detail_visible == visible:
            return
        self._detail_visible = visible
        for port in self.ports.values():
            port.setVisible(visible)
        for editor in self.parameter_editors.values():
            editor.setVisible(visible)
        self.update()

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
        del widget
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
            token = (
                "error"
                if any(
                    issue.severity is ValidationSeverity.ERROR for issue in self.view_model.issues
                )
                else "warning"
            )
            painter.setBrush(QBrush(self.theme.color(token)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QPointF(metrics.node_width - 17.0, metrics.header_height / 2.0), 6.0, 6.0
            )
        level_of_detail = option.levelOfDetailFromTransform(painter.worldTransform())
        if self.view_model.collapsed or not self._detail_visible or level_of_detail < 0.35:
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
        if change is QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and bool(value):
            self.ensure_parameter_editors()
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
        self.setToolTip(
            _diagnostic_tooltip(
                f"{view_model.source_port_id} → {view_model.destination_port_id}",
                view_model.issues,
            )
        )

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
    def selected_group_positions(self) -> dict[UUID, tuple[float, float]]: ...
    def commit_group_move(self, origins: dict[UUID, tuple[float, float]]) -> None: ...
    def edit_group(self, group_id: UUID) -> None: ...
