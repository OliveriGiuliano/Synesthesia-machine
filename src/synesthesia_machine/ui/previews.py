"""Qt-only rendering for immutable image and note previews from EngineClient."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from synesthesia_machine.contracts import ImagePreview, NotePreview


class ImagePreviewWidget(QWidget):
    """Render copied EngineClient preview bytes over an alpha checkerboard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.latest_preview: ImagePreview | None = None
        self._pixmap = QPixmap()
        self.setObjectName("image_preview_widget")
        self.setAccessibleName("Image data preview")
        self.setMinimumSize(260, 180)

    def sizeHint(self) -> QSize:
        return QSize(560, 300)

    def set_preview(self, preview: ImagePreview) -> None:
        image_format = (
            QImage.Format.Format_RGB888 if preview.channels == 3 else QImage.Format.Format_RGBA8888
        )
        bytes_per_line = preview.width * preview.channels
        image = QImage(
            preview.data.data,
            preview.width,
            preview.height,
            bytes_per_line,
            image_format,
        ).copy()
        self.latest_preview = preview
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#161a22"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#8d96a8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image preview")
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
    """Render a compact 128-key activity strip; no graph rendering runs in the engine."""

    _BLACK_PITCH_CLASSES = frozenset({1, 3, 6, 8, 10})

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.latest_preview: NotePreview | None = None
        self.setObjectName("note_preview_widget")
        self.setAccessibleName("MIDI note activity preview")
        self.setMinimumSize(260, 90)

    def sizeHint(self) -> QSize:
        return QSize(560, 130)

    def set_preview(self, preview: NotePreview) -> None:
        self.latest_preview = preview
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#161a22"))
        keyboard = self.rect().adjusted(8, 22, -8, -22)
        key_width = keyboard.width() / 128.0
        active: dict[int, int] = {}
        for activity in self.latest_preview.notes if self.latest_preview is not None else ():
            active[activity.note] = max(active.get(activity.note, 0), activity.velocity, 1)
        for note in range(128):
            key = QRectF(
                keyboard.left() + note * key_width,
                keyboard.top(),
                max(1.0, key_width),
                keyboard.height(),
            )
            velocity = active.get(note)
            if velocity is not None:
                strength = velocity / 127.0
                colour = QColor.fromHsvF(0.48, 0.75, 0.55 + 0.45 * strength)
            elif note % 12 in self._BLACK_PITCH_CLASSES:
                colour = QColor("#343a46")
            else:
                colour = QColor("#dce1e8")
            painter.fillRect(key, colour)
        painter.setPen(QColor("#8d96a8"))
        count = len(active)
        label = f"{count} active note{'s' if count != 1 else ''}"
        painter.drawText(8, 16, label)


class RuntimePreviewPanel(QWidget):
    """Visible Phase 3 image and note preview surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runtime_preview_panel")
        self.setAccessibleName("Runtime previews")
        self.image_widget = ImagePreviewWidget(self)
        self.note_widget = NotePreviewWidget(self)
        self.image_caption = QLabel("Waiting for Display Image Data…", self)
        self.note_caption = QLabel("Waiting for Note Visualizer…", self)
        self.image_caption.setObjectName("image_preview_caption")
        self.note_caption.setObjectName("note_preview_caption")

        image_page = QWidget(self)
        image_layout = QVBoxLayout(image_page)
        image_layout.setContentsMargins(4, 4, 4, 4)
        image_layout.addWidget(self.image_caption)
        image_layout.addWidget(self.image_widget, 1)

        note_page = QWidget(self)
        note_layout = QVBoxLayout(note_page)
        note_layout.setContentsMargins(4, 4, 4, 4)
        note_layout.addWidget(self.note_caption)
        note_layout.addWidget(self.note_widget, 1)

        self.tabs = QTabWidget(self)
        self.tabs.setAccessibleName("Runtime preview types")
        self.tabs.addTab(image_page, "Image")
        self.tabs.addTab(note_page, "Notes")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def show_image_preview(self, preview: ImagePreview) -> None:
        self.image_widget.set_preview(preview)
        self.image_caption.setText(
            f"Node {str(preview.node_id)[:8]} · tick {preview.tick_index} · "
            f"{preview.width}x{preview.height} · sequence {preview.sequence}"
        )

    def show_note_preview(self, preview: NotePreview) -> None:
        self.note_widget.set_preview(preview)
        self.note_caption.setText(
            f"Node {str(preview.node_id)[:8]} · tick {preview.tick_index} · "
            f"{len(preview.notes)} active · sequence {preview.sequence}"
        )


__all__ = ["ImagePreviewWidget", "NotePreviewWidget", "RuntimePreviewPanel"]
