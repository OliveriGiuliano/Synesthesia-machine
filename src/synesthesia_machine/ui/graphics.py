"""Custom event-driven graphics objects for nodes, ports, and Bézier cables."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import cast
from uuid import UUID

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
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
from synesthesia_machine.ui.parameter_editors import create_parameter_editor, parameter_tooltip
from synesthesia_machine.ui.theme import Theme, node_category_color, port_color_name
from synesthesia_machine.ui.tooltips import format_tooltip
from synesthesia_machine.ui.translations import tr
from synesthesia_machine.ui.view_models import (
    ConnectionViewModel,
    NodeViewModel,
    ParameterViewModel,
    PortViewModel,
)

type ParameterChangeHandler = Callable[[UUID, str, LiteralValue], None]


def _issue_tooltip(issues: tuple[ValidationIssue, ...]) -> str:
    return format_tooltip(_issue_text(issues))


def _diagnostic_tooltip(description: str, issues: tuple[ValidationIssue, ...]) -> str:
    if not issues:
        return format_tooltip(description)
    return format_tooltip(f"{description}\n\n{_issue_text(issues)}")


def _issue_text(issues: tuple[ValidationIssue, ...]) -> str:
    return "\n".join(
        f"{tr(str(issue.severity))} · {tr(issue.message)} ({issue.code})" for issue in issues
    )


class GroupGraphicsItem(QGraphicsObject):
    """Movable persisted group/comment projection rendered behind graph nodes."""

    _RESIZE_MARGIN = 8.0
    _MINIMUM_SIZE = (120.0, 80.0)
    _PAINT_MARGIN = 5.0

    def __init__(self, model: GroupModel, theme: Theme) -> None:
        super().__init__()
        self.model = model
        self.theme = theme
        self._drag_origin: dict[UUID, tuple[float, float]] = {}
        self._display_size = model.size
        self._resize_edges: tuple[bool, bool, bool, bool] | None = None
        self._resize_start_scene = QPointF()
        self._resize_start_position = model.position
        self._resize_start_size = model.size
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.setPos(*model.position)
        self.setZValue(-3.0 if model.kind is GroupKind.GROUP else -0.75)
        self.setToolTip(format_tooltip(model.text or model.title))
        self.setAcceptHoverEvents(True)

    def boundingRect(self) -> QRectF:
        return self._body_rect().adjusted(
            -self._PAINT_MARGIN,
            -self._PAINT_MARGIN,
            self._PAINT_MARGIN,
            self._PAINT_MARGIN,
        )

    def _body_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._display_size[0], self._display_size[1])

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        body = self._body_rect()
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
        if self.isSelected():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self.theme.color("selection")))
            handle = 6.0
            for point in (body.topLeft(), body.topRight(), body.bottomLeft(), body.bottomRight()):
                painter.drawRect(
                    QRectF(point.x() - handle / 2.0, point.y() - handle / 2.0, handle, handle)
                )

    def _edges_at(self, position: QPointF) -> tuple[bool, bool, bool, bool] | None:
        body = self._body_rect()
        if not body.adjusted(
            -self._RESIZE_MARGIN,
            -self._RESIZE_MARGIN,
            self._RESIZE_MARGIN,
            self._RESIZE_MARGIN,
        ).contains(position):
            return None
        left = abs(position.x() - body.left()) <= self._RESIZE_MARGIN
        top = abs(position.y() - body.top()) <= self._RESIZE_MARGIN
        right = abs(position.x() - body.right()) <= self._RESIZE_MARGIN
        bottom = abs(position.y() - body.bottom()) <= self._RESIZE_MARGIN
        edges = (left, top, right, bottom)
        return edges if any(edges) else None

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        edges = self._edges_at(event.pos())
        if edges is None:
            self.unsetCursor()
        elif (edges[0] and edges[1]) or (edges[2] and edges[3]):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif (edges[2] and edges[1]) or (edges[0] and edges[3]):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges[0] or edges[2]:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self._resize_edges is None:
            self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        edges = self._edges_at(event.pos())
        if event.button() is Qt.MouseButton.LeftButton and edges is not None:
            self.setSelected(True)
            self._resize_edges = edges
            self._resize_start_scene = event.scenePos()
            self._resize_start_position = (self.pos().x(), self.pos().y())
            self._resize_start_size = self._display_size
            event.accept()
            return
        super().mousePressEvent(event)
        scene = cast("GraphSceneProtocol", self.scene())
        self._drag_origin = scene.selected_group_positions()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_edges is None:
            super().mouseMoveEvent(event)
            return
        delta = event.scenePos() - self._resize_start_scene
        left, top = self._resize_start_position
        right = left + self._resize_start_size[0]
        bottom = top + self._resize_start_size[1]
        resize_left, resize_top, resize_right, resize_bottom = self._resize_edges
        if resize_left:
            left = min(right - self._MINIMUM_SIZE[0], left + delta.x())
        if resize_top:
            top = min(bottom - self._MINIMUM_SIZE[1], top + delta.y())
        if resize_right:
            right = max(left + self._MINIMUM_SIZE[0], right + delta.x())
        if resize_bottom:
            bottom = max(top + self._MINIMUM_SIZE[1], bottom + delta.y())
        self.prepareGeometryChange()
        self._display_size = (right - left, bottom - top)
        self.setPos(left, top)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_edges is not None:
            self._resize_edges = None
            position = (self.pos().x(), self.pos().y())
            size = self._display_size
            self.unsetCursor()
            cast("GraphSceneProtocol", self.scene()).commit_group_resize(
                self.model.id, position, size
            )
            event.accept()
            return
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
        direction = tr("output" if view_model.is_output else "input")
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

    _EDITOR_WIDTH = 150.0
    _EDITOR_RIGHT_MARGIN = 10.0
    _LABEL_LEFT = 13.0
    _LABEL_EDITOR_GAP = 10.0

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
        self._heat_level: float | None = None
        self._drag_origin: dict[UUID, tuple[float, float]] = {}
        self._width = self._layout_width()
        self._height = self._layout_height()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setAcceptHoverEvents(True)
        self.setPos(*view_model.position)
        self.setToolTip(format_tooltip(view_model.description))
        self._create_ports_and_editors()

    @property
    def heat_level(self) -> float | None:
        return self._heat_level

    def set_heat_level(self, level: float | None) -> None:
        normalized = None if level is None else min(1.0, max(0.0, level))
        if normalized == self._heat_level:
            return
        self._heat_level = normalized
        self.update()

    def _layout_height(self) -> float:
        if self.view_model.collapsed:
            return self.theme.metrics.header_height
        ordinary_inputs = sum(not port.is_parameter for port in self.view_model.inputs)
        rows = ordinary_inputs + len(self.view_model.parameters) + len(self.view_model.outputs)
        return self.theme.metrics.header_height + max(rows, 1) * self.theme.metrics.row_height + 8.0

    def _layout_width(self) -> float:
        metrics = self.theme.metrics
        title_width = QFontMetricsF(self.theme.title_font()).horizontalAdvance(
            self.view_model.title
        )
        required = title_width + 50.0
        if self.view_model.parameters:
            label_metrics = QFontMetricsF(self.theme.body_font())
            longest_label = max(
                label_metrics.horizontalAdvance(parameter.spec.label)
                for parameter in self.view_model.parameters
            )
            required = max(
                required,
                self._LABEL_LEFT
                + longest_label
                + self._LABEL_EDITOR_GAP
                + self._EDITOR_WIDTH
                + self._EDITOR_RIGHT_MARGIN,
            )
        return max(metrics.node_width, required)

    @property
    def node_width(self) -> float:
        return self._width

    @property
    def body_scene_rect(self) -> QRectF:
        """Painted node-body bounds, excluding port and interaction margins."""
        body = QRectF(0.0, 0.0, self._width, self._height)
        return self.mapRectToScene(body)

    def _editor_left(self) -> float:
        return self._width - self._EDITOR_WIDTH - self._EDITOR_RIGHT_MARGIN

    def _issue_badge_rect(self) -> QRectF:
        center = QPointF(self._width - 17.0, self.theme.metrics.header_height / 2.0)
        return QRectF(center.x() - 6.0, center.y() - 6.0, 12.0, 12.0)

    def _issue_badge_hit_rect(self) -> QRectF:
        return self._issue_badge_rect().adjusted(-4.0, -4.0, 4.0, 4.0)

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
            child.setPos(self._width, y)
            self.ports[(port.port_id, True)] = child
            y += metrics.row_height

    def _create_parameter_editor(self, parameter: ParameterViewModel, y: float) -> None:
        callback = partial(
            self._on_parameter_changed,
            self.view_model.node_id,
            parameter.spec.id,
        )
        editor = create_parameter_editor(parameter, callback, compact=True)
        editor.setFixedWidth(round(self._EDITOR_WIDTH))
        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(editor)
        proxy.setPos(
            self._editor_left(),
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
            self._width + margin * 2.0,
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
        body = QRectF(0.0, 0.0, self._width, self._height)
        category_color = node_category_color(self.view_model.category)
        border_color = category_color
        border_width = 1.4
        if self._heat_level is not None:
            border_color = _interpolate_color(
                category_color, self.theme.color("error"), self._heat_level
            )
            border_width = 1.5 + self._heat_level * 2.5
        pen = QPen(border_color)
        pen.setWidthF(border_width)
        painter.setPen(pen)
        painter.setBrush(QBrush(self.theme.color("node")))
        painter.drawRoundedRect(body, metrics.node_radius, metrics.node_radius)
        painter.setBrush(QBrush(self.theme.color("node_header")))
        painter.drawRoundedRect(
            QRectF(0.0, 0.0, self._width, metrics.header_height),
            metrics.node_radius,
            metrics.node_radius,
        )
        painter.setPen(category_color)
        painter.setFont(self.theme.title_font())
        painter.drawText(
            QRectF(12.0, 0.0, self._width - 38.0, metrics.header_height),
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
            badge = self._issue_badge_rect()
            painter.drawEllipse(badge)
            badge_font = self.theme.body_font()
            badge_font.setBold(True)
            badge_font.setPointSizeF(8.0)
            painter.setFont(badge_font)
            painter.setPen(self.theme.color("text"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "!")
        level_of_detail = option.levelOfDetailFromTransform(painter.worldTransform())
        if self.view_model.collapsed or not self._detail_visible or level_of_detail < 0.35:
            return
        painter.setFont(self.theme.body_font())
        y = metrics.header_height
        for port in (item for item in self.view_model.inputs if not item.is_parameter):
            self._draw_row(painter, y, port.label, port.type_name, False)
            y += metrics.row_height
        for parameter in self.view_model.parameters:
            detail = tr("Live input") if parameter.connected else ""
            self._draw_row(
                painter,
                y,
                parameter.spec.label,
                detail,
                False,
                parameter.connected,
                has_editor=True,
            )
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
        *,
        has_editor: bool = False,
    ) -> None:
        metrics = self.theme.metrics
        painter.setPen(self.theme.color("disabled" if disabled else "text"))
        alignment = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft
        )
        label_width = (
            self._editor_left() - self._LABEL_EDITOR_GAP - self._LABEL_LEFT
            if has_editor
            else self._width * 0.42
        )
        painter.drawText(QRectF(13.0, y, label_width, metrics.row_height), alignment, label)
        if not right and detail:
            painter.setPen(self.theme.color("muted_text" if not disabled else "disabled"))
            detail_left = self._editor_left() if has_editor else self._width * 0.42
            detail_width = self._EDITOR_WIDTH if has_editor else self._width * 0.52
            painter.drawText(
                QRectF(detail_left, y, detail_width, metrics.row_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                detail,
            )

    def _tooltip_for_position(self, position: QPointF) -> str:
        node_help = format_tooltip(self.view_model.description)
        if self.view_model.issues and self._issue_badge_hit_rect().contains(position):
            return _issue_tooltip(self.view_model.issues)
        if position.y() < self.theme.metrics.header_height:
            return node_help
        if position.x() > self._editor_left() - self._LABEL_EDITOR_GAP:
            return node_help
        y = self.theme.metrics.header_height
        y += sum(not port.is_parameter for port in self.view_model.inputs) * (
            self.theme.metrics.row_height
        )
        for parameter in self.view_model.parameters:
            if y <= position.y() < y + self.theme.metrics.row_height:
                return format_tooltip(parameter_tooltip(parameter.spec))
            y += self.theme.metrics.row_height
        return node_help

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setToolTip(self._tooltip_for_position(event.pos()))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setToolTip(format_tooltip(self.view_model.description))
        super().hoverLeaveEvent(event)

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


def _interpolate_color(start: QColor, end: QColor, amount: float) -> QColor:
    inverse = 1.0 - amount
    return QColor.fromRgbF(
        start.redF() * inverse + end.redF() * amount,
        start.greenF() * inverse + end.greenF() * amount,
        start.blueF() * inverse + end.blueF() * amount,
        1.0,
    )


class ConnectionGraphicsItem(QGraphicsObject):
    """Bézier cable with a type-aware live value pill at its midpoint.

    The pill shows the producer's latest number for INT/FLOAT links or a
    scaled thumbnail for IMAGE/CHANNEL links. A small chevron on the right
    hides or shows the pill for this connection only; the hidden state persists
    through the document and remains undoable.
    """

    togglePreviewRequested = Signal(UUID)

    _PILL_TEXT_V_PADDING = 4.0
    _PILL_PADDING = 8.0
    _PILL_THUMB_PADDING = 5.0
    _PILL_CHEVRON = 16.0
    _PILL_THUMB = 204.0
    _PILL_THUMB_HEIGHT = 120.0
    _PILL_CORNER_RADIUS = 8.0
    _PILL_TEXT_LIMIT = 12
    _PILL_COLLAPSED_RADIUS = 3.5
    _PILL_COLLAPSED_HIT = 8.0

    def __init__(self, view_model: ConnectionViewModel, theme: Theme) -> None:
        super().__init__()
        self.view_model = view_model
        self.theme = theme
        self.path = QPainterPath()
        self._midpoint: QPointF | None = None
        self._preview_visible = True
        self._value_text: str | None = None
        self._image: QImage | None = None
        self.setZValue(-1.0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setToolTip(
            _diagnostic_tooltip(
                f"{view_model.source_port_id} → {view_model.destination_port_id}\n"
                + tr("Double-click to inspect this live connection."),
                view_model.issues,
            )
        )

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self.prepareGeometryChange()
        self.path = bezier_path(start, end)
        self._midpoint = self.path.pointAtPercent(0.5) if not self.path.isEmpty() else None
        self.update()

    def set_preview_visible(self, visible: bool) -> None:
        if visible == self._preview_visible:
            return
        self.prepareGeometryChange()
        self._preview_visible = visible
        self.update()

    def set_value_preview(self, text: str | None) -> None:
        if text == self._value_text:
            return
        self.prepareGeometryChange()
        self._value_text = text
        self.update()

    def set_image_preview(self, image: QImage | None) -> None:
        if image is self._image:
            return
        self.prepareGeometryChange()
        self._image = image
        self.update()

    def takes_value_pill(self) -> bool:
        return self._pill_family() == "scalar"

    def takes_image_pill(self) -> bool:
        return self._pill_family() in ("image", "channel")

    @property
    def preview_scene_rect(self) -> QRectF:
        """Visible pill bounds used to keep organized nodes clear of previews."""

        if not self._preview_visible:
            return QRectF()
        body = self._pill_body_rect()
        if body is None:
            return QRectF()
        chevron = self._chevron_rect()
        if chevron is not None:
            body = body.united(chevron)
        return self.mapRectToScene(body)

    def _pill_family(self) -> str:
        type_name = self.view_model.type_name
        if type_name in ("INT", "FLOAT"):
            return "scalar"
        if type_name in ("IMAGE", "CHANNEL"):
            return type_name.lower()
        return ""

    def _pill_padding(self) -> float:
        return self._PILL_PADDING if self._pill_family() == "scalar" else self._PILL_THUMB_PADDING

    def _pill_text(self) -> str:
        text = self._value_text
        if text is None:
            return ""
        if len(text) > self._PILL_TEXT_LIMIT:
            text = text[: self._PILL_TEXT_LIMIT - 1] + "…"
        return text

    def _thumbnail_size(self) -> tuple[float, float]:
        """Fit the live frame into the pill target box, preserving its aspect ratio.

        The outline is then sized to hug this fitted frame (plus padding) so the
        pill follows the video's shape instead of a fixed rectangle.
        """
        image = self._image
        if image is None or image.isNull() or image.width() <= 0 or image.height() <= 0:
            return self._PILL_THUMB, self._PILL_THUMB_HEIGHT
        scale = min(
            self._PILL_THUMB / image.width(),
            self._PILL_THUMB_HEIGHT / image.height(),
        )
        return image.width() * scale, image.height() * scale

    def _pill_body_rect(self) -> QRectF | None:
        if self._pill_family() == "" or self._midpoint is None:
            return None
        if self._pill_family() == "scalar":
            metrics = QFontMetricsF(self.theme.body_font())
            content = metrics.horizontalAdvance(self._pill_text())
            # Size the badge from the font line height so the number is never
            # clipped, regardless of DPI or font substitution.
            height = metrics.height() + self._PILL_TEXT_V_PADDING * 2.0
        else:
            thumb_width, thumb_height = self._thumbnail_size()
            content = thumb_width
            height = thumb_height + self._PILL_THUMB_PADDING * 2.0
        padding = self._pill_padding()
        width = content + padding * 2.0 + self._PILL_CHEVRON
        return QRectF(
            self._midpoint.x() - width / 2.0,
            self._midpoint.y() - height / 2.0,
            width,
            height,
        )

    def _pill_content_rect(self, body: QRectF) -> QRectF:
        padding = self._pill_padding()
        # The scalar body height already reserves _PILL_TEXT_V_PADDING around the
        # font, so centre the text across the full body height (no vertical clip);
        # image pills inset their thumbnail by the thumb padding instead.
        v_pad = 0.0 if self._pill_family() == "scalar" else padding
        return body.adjusted(padding, v_pad, -(self._PILL_CHEVRON + padding), -v_pad)

    def _chevron_rect(self) -> QRectF | None:
        if self._pill_family() == "" or self._midpoint is None:
            return None
        if not self._preview_visible:
            mid = self._midpoint
            return QRectF(
                mid.x() - self._PILL_COLLAPSED_HIT,
                mid.y() - self._PILL_COLLAPSED_HIT,
                self._PILL_COLLAPSED_HIT * 2.0,
                self._PILL_COLLAPSED_HIT * 2.0,
            )
        body = self._pill_body_rect()
        if body is None:
            return None
        width = self._PILL_CHEVRON + 6.0
        return QRectF(body.right() - width, body.top() - 3.0, width, body.height() + 6.0)

    def boundingRect(self) -> QRectF:
        rect = self.path.boundingRect().adjusted(-6.0, -6.0, 6.0, 6.0)
        for extra in (self._chevron_rect(), self._pill_body_rect()):
            if extra is not None:
                rect = rect.united(extra)
        return rect

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(self.theme.metrics.cable_hit_width)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        shape = stroker.createStroke(self.path)
        chevron = self._chevron_rect()
        if chevron is not None:
            chevron_path = QPainterPath()
            chevron_path.addRect(chevron)
            shape = shape.united(chevron_path)
        return shape

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
        self._paint_pill(painter)

    def _paint_pill(self, painter: QPainter) -> None:
        if self._pill_family() == "" or self._midpoint is None:
            return
        if not self._preview_visible:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.theme.color("muted_text"))
            radius = self._PILL_COLLAPSED_RADIUS
            painter.drawEllipse(self._midpoint, radius, radius)
            return
        body = self._pill_body_rect()
        if body is None:
            return
        # Video pills use a small radius so the outline hugs the frame; scalar
        # value pills keep the fully-rounded (stadium) badge look, so the radius
        # follows the font-sized body height.
        radius = (
            body.height() / 2.0 if self._pill_family() == "scalar" else self._PILL_CORNER_RADIUS
        )
        painter.setPen(QPen(self.theme.color("border"), 1.0))
        painter.setBrush(self.theme.color("panel"))
        painter.drawRoundedRect(body, radius, radius)
        content = self._pill_content_rect(body)
        if self._pill_family() == "scalar":
            text = self._pill_text()
            if text:
                painter.setPen(self.theme.color("text"))
                painter.setFont(self.theme.body_font())
                painter.drawText(content, Qt.AlignmentFlag.AlignCenter, text)
        else:
            image = self._image
            if image is not None and not image.isNull():
                padding = self._pill_padding()
                # Clip the frame to the body inset by the padding, with a radius
                # concentric to the outline (radius - padding). This keeps a uniform
                # gap between the frame and the border at the corners, so the sharp
                # frame corners never graze the rounded outline.
                inner_radius = max(0.0, radius - padding)
                clip = QPainterPath()
                clip.addRoundedRect(
                    body.adjusted(padding, padding, -padding, -padding),
                    inner_radius,
                    inner_radius,
                )
                painter.save()
                painter.setClipPath(clip)
                self._draw_thumbnail(painter, content, image, self._pill_family() == "channel")
                painter.restore()
        self._draw_chevron(painter, body)

    def _draw_thumbnail(
        self, painter: QPainter, rect: QRectF, image: QImage, grayscale: bool
    ) -> None:
        if grayscale:
            image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        scaled = image.size().scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.isEmpty():
            return
        target = QRectF(
            rect.center().x() - scaled.width() / 2.0,
            rect.center().y() - scaled.height() / 2.0,
            scaled.width(),
            scaled.height(),
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(target, image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def _draw_chevron(self, painter: QPainter, body: QRectF) -> None:
        cx = body.right() - self._PILL_CHEVRON / 2.0
        cy = body.center().y()
        path = QPainterPath()
        path.moveTo(cx - 3.0, cy - 1.5)
        path.lineTo(cx, cy + 1.5)
        path.lineTo(cx + 3.0, cy - 1.5)
        pen = QPen(self.theme.color("muted_text"), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        chevron = self._chevron_rect()
        if chevron is not None and chevron.contains(event.pos()):
            self.togglePreviewRequested.emit(self.view_model.connection_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = cast("GraphSceneProtocol", self.scene())
        scene.inspect_connection(self.view_model.connection_id)
        event.accept()


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
        painter.setBrush(Qt.BrushStyle.NoBrush)
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
    def commit_group_resize(
        self,
        group_id: UUID,
        position: tuple[float, float],
        size: tuple[float, float],
    ) -> None: ...
    def edit_group(self, group_id: UUID) -> None: ...
    def inspect_connection(self, connection_id: UUID) -> None: ...
