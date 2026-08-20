"""Qt-only rendering for immutable image and note previews from EngineClient."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from synesthesia_machine.contracts import ImagePreview, NotePreview
from synesthesia_machine.ui.translations import tr, trf


def image_preview_to_qimage(preview: ImagePreview) -> QImage:
    """Build an owned QImage copy from immutable preview bytes.

    The returned image copies the buffer so callers may draw it asynchronously
    without retaining the underlying preview memory.
    """
    image_format = (
        QImage.Format.Format_RGB888 if preview.channels == 3 else QImage.Format.Format_RGBA8888
    )
    bytes_per_line = preview.width * preview.channels
    return QImage(
        preview.data.data,
        preview.width,
        preview.height,
        bytes_per_line,
        image_format,
    ).copy()


class ImagePreviewWidget(QWidget):
    """Render copied EngineClient preview bytes over an alpha checkerboard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.latest_preview: ImagePreview | None = None
        self._pixmap = QPixmap()
        self.setObjectName("image_preview_widget")
        self.setAccessibleName(tr("Image data preview"))
        self.setMinimumSize(260, 180)

    def sizeHint(self) -> QSize:
        return QSize(560, 300)

    def set_preview(self, preview: ImagePreview) -> None:
        self.latest_preview = preview
        self._pixmap = QPixmap.fromImage(image_preview_to_qimage(preview))
        self.update()

    def clear_preview(self) -> None:
        self.latest_preview = None
        self._pixmap = QPixmap()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#161a22"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#8d96a8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("No image preview"))
            return

        target_size = self._pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        left = (self.width() - target_size.width()) / 2.0
        top = (self.height() - target_size.height()) / 2.0
        target = QRectF(left, top, target_size.width(), target_size.height())
        self._paint_checkerboard(painter, target)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

    @staticmethod
    def _paint_checkerboard(painter: QPainter, target: QRectF) -> None:
        tile = 12
        painter.save()
        painter.setClipRect(target)
        for row, y in enumerate(range(int(target.top()), int(target.bottom()) + tile, tile)):
            for column, x in enumerate(range(int(target.left()), int(target.right()) + tile, tile)):
                colour = QColor("#7f8794") if (row + column) % 2 else QColor("#b3bac4")
                painter.fillRect(x, y, tile, tile, colour)
        painter.restore()


class NotePreviewWidget(QWidget):
    """Render active note velocities as an adaptive rainbow bar chart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.latest_preview: NotePreview | None = None
        self.setObjectName("note_preview_widget")
        self.setAccessibleName(tr("MIDI note velocity chart"))
        self.setMinimumSize(180, 120)

    def sizeHint(self) -> QSize:
        return QSize(560, 130)

    def set_preview(self, preview: NotePreview) -> None:
        self.latest_preview = preview
        self.update()

    def clear_preview(self) -> None:
        self.latest_preview = None
        self.update()

    def note_axis_orientation(self) -> Qt.Orientation:
        """Return the chart orientation used at the current widget aspect ratio."""

        return (
            Qt.Orientation.Horizontal if self.width() >= self.height() else Qt.Orientation.Vertical
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#161a22"))
        active: dict[int, int] = {}
        for activity in self.latest_preview.notes if self.latest_preview is not None else ():
            active[activity.note] = max(active.get(activity.note, 0), activity.velocity, 1)

        orientation = self.note_axis_orientation()
        chart = (
            QRectF(self.rect().adjusted(34, 28, -12, -24))
            if orientation is Qt.Orientation.Horizontal
            else QRectF(self.rect().adjusted(34, 28, -32, -22))
        )
        if chart.width() <= 0.0 or chart.height() <= 0.0:
            return
        self._paint_grid(painter, chart, orientation)
        if orientation is Qt.Orientation.Horizontal:
            self._paint_horizontal_bars(painter, chart, active)
        else:
            self._paint_vertical_note_axis_bars(painter, chart, active)

        painter.setPen(QColor("#a8b3c7"))
        count = len(active)
        label = trf("{count} active note(s) · velocity 0-127", count=count)
        painter.drawText(8, 18, label)

    @staticmethod
    def _paint_grid(
        painter: QPainter,
        chart: QRectF,
        orientation: Qt.Orientation,
    ) -> None:
        painter.setPen(QPen(QColor("#36404d"), 1.0))
        for fraction in (0.0, 0.5, 1.0):
            if orientation is Qt.Orientation.Horizontal:
                coordinate = chart.bottom() - chart.height() * fraction
                painter.drawLine(
                    QPointF(chart.left(), coordinate), QPointF(chart.right(), coordinate)
                )
            else:
                coordinate = chart.left() + chart.width() * fraction
                painter.drawLine(
                    QPointF(coordinate, chart.top()), QPointF(coordinate, chart.bottom())
                )
        painter.setPen(QColor("#7f8b99"))
        if orientation is Qt.Orientation.Horizontal:
            for velocity, fraction in ((0, 0.0), (64, 0.5), (127, 1.0)):
                coordinate = chart.bottom() - chart.height() * fraction
                painter.drawText(
                    QRectF(0.0, coordinate - 8.0, chart.left() - 4.0, 16.0),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(velocity),
                )
            for note, fraction, alignment in (
                (0, 0.0, Qt.AlignmentFlag.AlignLeft),
                (64, 0.5, Qt.AlignmentFlag.AlignHCenter),
                (127, 1.0, Qt.AlignmentFlag.AlignRight),
            ):
                coordinate = chart.left() + chart.width() * fraction
                painter.drawText(
                    QRectF(coordinate - 22.0, chart.bottom() + 3.0, 44.0, 18.0),
                    alignment | Qt.AlignmentFlag.AlignTop,
                    str(note),
                )
        else:
            for note, fraction in ((0, 0.0), (64, 0.5), (127, 1.0)):
                coordinate = chart.bottom() - chart.height() * fraction
                painter.drawText(
                    QRectF(0.0, coordinate - 8.0, chart.left() - 4.0, 16.0),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(note),
                )
            for velocity, fraction, alignment in (
                (0, 0.0, Qt.AlignmentFlag.AlignLeft),
                (64, 0.5, Qt.AlignmentFlag.AlignHCenter),
                (127, 1.0, Qt.AlignmentFlag.AlignRight),
            ):
                coordinate = chart.left() + chart.width() * fraction
                painter.drawText(
                    QRectF(coordinate - 22.0, chart.bottom() + 3.0, 44.0, 18.0),
                    alignment | Qt.AlignmentFlag.AlignTop,
                    str(velocity),
                )

    @staticmethod
    def _paint_horizontal_bars(
        painter: QPainter,
        chart: QRectF,
        active: dict[int, int],
    ) -> None:
        slot = chart.width() / 128.0
        painter.setPen(Qt.PenStyle.NoPen)
        for note, velocity in active.items():
            bar_height = chart.height() * velocity / 127.0
            bar_width = max(1.0, slot * 0.82)
            bar = QRectF(
                chart.left() + note * slot + (slot - bar_width) / 2.0,
                chart.bottom() - bar_height,
                bar_width,
                bar_height,
            )
            painter.fillRect(bar, note_rainbow_color(note))

    @staticmethod
    def _paint_vertical_note_axis_bars(
        painter: QPainter,
        chart: QRectF,
        active: dict[int, int],
    ) -> None:
        slot = chart.height() / 128.0
        painter.setPen(Qt.PenStyle.NoPen)
        for note, velocity in active.items():
            bar_height = max(1.0, slot * 0.82)
            bar_width = chart.width() * velocity / 127.0
            bar = QRectF(
                chart.left(),
                chart.bottom() - (note + 1) * slot + (slot - bar_height) / 2.0,
                bar_width,
                bar_height,
            )
            painter.fillRect(bar, note_rainbow_color(note))


def note_rainbow_color(note: int) -> QColor:
    """Map MIDI note 0..127 onto a readable red-to-violet rainbow gradient."""

    clamped = min(127, max(0, note))
    return QColor.fromHsvF(0.78 * clamped / 127.0, 0.68, 0.95)


class ImagePreviewPanel(QWidget):
    """Self-contained image preview page suitable for its own dock or tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("image_preview_panel")
        self.setAccessibleName(tr("Image preview"))
        self.image_widget = ImagePreviewWidget(self)
        self.image_caption = QLabel(tr("Waiting for Display Image Data…"), self)
        self.image_caption.setObjectName("image_preview_caption")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.image_caption)
        layout.addWidget(self.image_widget, 1)

    def show_preview(self, preview: ImagePreview) -> None:
        self.image_widget.set_preview(preview)
        self.image_caption.setText(
            trf(
                "Node {node} · tick {tick} · {width}x{height} · sequence {sequence}",
                node=str(preview.owner_id)[:8],
                tick=preview.tick_index,
                width=preview.width,
                height=preview.height,
                sequence=preview.sequence,
            )
        )

    def clear_preview(self) -> None:
        self.image_widget.clear_preview()
        self.image_caption.setText(tr("Waiting for Display Image Data…"))

    def retranslate(self) -> None:
        self.setAccessibleName(tr("Image preview"))
        self.image_widget.setAccessibleName(tr("Image data preview"))
        if self.image_widget.latest_preview is None:
            self.image_caption.setText(tr("Waiting for Display Image Data…"))
        else:
            self.show_preview(self.image_widget.latest_preview)
        self.image_widget.update()


class NotePreviewPanel(QWidget):
    """Self-contained note velocity page suitable for its own dock or tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("note_preview_panel")
        self.setAccessibleName(tr("Note visualizer"))
        self.note_widget = NotePreviewWidget(self)
        self.note_caption = QLabel(tr("Waiting for Note Visualizer…"), self)
        self.note_caption.setObjectName("note_preview_caption")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.note_caption)
        layout.addWidget(self.note_widget, 1)

    def show_preview(self, preview: NotePreview) -> None:
        self.note_widget.set_preview(preview)
        self.note_caption.setText(
            trf(
                "Node {node} · tick {tick} · {count} active · sequence {sequence}",
                node=str(preview.owner_id)[:8],
                tick=preview.tick_index,
                count=len(preview.notes),
                sequence=preview.sequence,
            )
        )

    def clear_preview(self) -> None:
        self.note_widget.clear_preview()
        self.note_caption.setText(tr("Waiting for Note Visualizer…"))

    def retranslate(self) -> None:
        self.setAccessibleName(tr("Note visualizer"))
        self.note_widget.setAccessibleName(tr("MIDI note velocity chart"))
        if self.note_widget.latest_preview is None:
            self.note_caption.setText(tr("Waiting for Note Visualizer…"))
        else:
            self.show_preview(self.note_widget.latest_preview)
        self.note_widget.update()


class RuntimePreviewPanel(QWidget):
    """Tabbed compatibility surface composed from independent preview pages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runtime_preview_panel")
        self.setAccessibleName(tr("Runtime previews"))
        self.image_panel = ImagePreviewPanel(self)
        self.note_panel = NotePreviewPanel(self)
        self.image_widget = self.image_panel.image_widget
        self.note_widget = self.note_panel.note_widget
        self.image_caption = self.image_panel.image_caption
        self.note_caption = self.note_panel.note_caption
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("runtime_preview_tabs")
        self.tabs.setAccessibleName(tr("Image and note preview tabs"))
        self.tabs.addTab(self.image_panel, tr("Image Preview"))
        self.tabs.addTab(self.note_panel, tr("Note Visualizer"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def show_image_preview(self, preview: ImagePreview) -> None:
        self.image_panel.show_preview(preview)

    def show_note_preview(self, preview: NotePreview) -> None:
        self.note_panel.show_preview(preview)


__all__ = [
    "ImagePreviewPanel",
    "ImagePreviewWidget",
    "NotePreviewPanel",
    "NotePreviewWidget",
    "RuntimePreviewPanel",
    "image_preview_to_qimage",
    "note_rainbow_color",
]
