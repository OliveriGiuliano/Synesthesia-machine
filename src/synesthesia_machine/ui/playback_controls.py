"""Seek and scrub controls for a Load Video source node in the inspector."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from synesthesia_machine.ui.parameter_editors import DirectDragSlider, format_timestamp
from synesthesia_machine.ui.translations import tr

_STEPS = 10_000
_REWIND_15_S = -15.0
_REWIND_5_S = -5.0
_FORWARD_5_S = 5.0
_FORWARD_15_S = 15.0


class VideoPlaybackControls(QWidget):
    """Progress slider and seek buttons for one video source.

    The slider and buttons only emit :attr:`seekRequested`; driving the
    engine (and reading the played position back) is the main window's job,
    which keeps this widget free of engine or document dependencies.
    """

    seekRequested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._position_s = 0.0
        self._duration_s: float | None = None

        self.progress_slider = DirectDragSlider(Qt.Orientation.Horizontal, self)
        self.progress_slider.setObjectName("video_progress_slider")
        self.progress_slider.setAccessibleName(tr("Video playback position slider"))
        self.progress_slider.setRange(0, _STEPS)
        self.progress_slider.setTickInterval(_STEPS // 10)

        self.rewind_15_button = QPushButton("««", self)
        self.rewind_15_button.setObjectName("video_rewind_15_button")
        self.rewind_15_button.setAccessibleName(tr("Rewind 15 seconds"))
        self.rewind_15_button.setToolTip(tr("Seek 15 seconds back"))
        self.rewind_5_button = QPushButton("«", self)
        self.rewind_5_button.setObjectName("video_rewind_5_button")
        self.rewind_5_button.setAccessibleName(tr("Rewind 5 seconds"))
        self.rewind_5_button.setToolTip(tr("Seek 5 seconds back"))
        self.forward_5_button = QPushButton("»", self)
        self.forward_5_button.setObjectName("video_forward_5_button")
        self.forward_5_button.setAccessibleName(tr("Forward 5 seconds"))
        self.forward_5_button.setToolTip(tr("Seek 5 seconds forward"))
        self.forward_15_button = QPushButton("»»", self)
        self.forward_15_button.setObjectName("video_forward_15_button")
        self.forward_15_button.setAccessibleName(tr("Forward 15 seconds"))
        self.forward_15_button.setToolTip(tr("Seek 15 seconds forward"))
        for button, delta in (
            (self.rewind_15_button, _REWIND_15_S),
            (self.rewind_5_button, _REWIND_5_S),
            (self.forward_5_button, _FORWARD_5_S),
            (self.forward_15_button, _FORWARD_15_S),
        ):
            button.setFixedWidth(28)
            button.clicked.connect(lambda _checked=False, d=delta: self._nudge(d))

        self.position_label = QLabel("00:00:00 / 00:00:00", self)
        self.position_label.setObjectName("video_position_label")
        self.position_label.setAccessibleName(tr("Video playback position"))
        self.position_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.position_label.setMinimumWidth(120)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.rewind_15_button)
        layout.addWidget(self.rewind_5_button)
        layout.addWidget(self.progress_slider, 1)
        layout.addWidget(self.forward_5_button)
        layout.addWidget(self.forward_15_button)
        layout.addWidget(self.position_label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.progress_slider.sliderReleased.connect(self._emit_slider_seek)
        for button in (
            self.rewind_15_button,
            self.rewind_5_button,
            self.forward_5_button,
            self.forward_15_button,
        ):
            button.setEnabled(False)
        self.progress_slider.setEnabled(False)
        self._render_position()

    def set_progress(self, position_s: float | None, duration_s: float | None) -> None:
        """Reflect the engine's reported playback position for this source.

        While the user holds the slider down, engine telemetry must not
        fight the in-flight scrub, so the position update is deferred.
        """

        scrubbing = self.progress_slider.isSliderDown()
        available = position_s is not None
        if available and not scrubbing:
            self._position_s = max(0.0, position_s or 0.0)
        self._duration_s = duration_s
        for child in (
            self.progress_slider,
            self.rewind_15_button,
            self.rewind_5_button,
            self.forward_5_button,
            self.forward_15_button,
        ):
            child.setEnabled(available)
        if not available:
            self._position_s = 0.0
        if not scrubbing:
            self._render_position()

    def _render_position(self) -> None:
        if self._duration_s:
            if self._position_s > self._duration_s:
                self._position_s = self._duration_s
            ratio = self._position_s / self._duration_s
        else:
            ratio = 0.0
        self.progress_slider.blockSignals(True)
        self.progress_slider.setValue(round(ratio * _STEPS))
        self.progress_slider.blockSignals(False)
        if self._duration_s is not None:
            self.position_label.setText(
                f"{format_timestamp(self._position_s)} / {format_timestamp(self._duration_s)}"
            )
        else:
            self.position_label.setText(format_timestamp(self._position_s))

    def _nudge(self, delta_s: float) -> None:
        target = self._position_s + delta_s
        target = max(0.0, target)
        if self._duration_s is not None:
            target = min(target, self._duration_s)
        self._position_s = target
        self._render_position()
        self.seekRequested.emit(target)

    def _emit_slider_seek(self) -> None:
        if self._duration_s:
            ratio = self.progress_slider.value() / _STEPS
            self._position_s = min(self._duration_s, max(0.0, ratio * self._duration_s))
        self.seekRequested.emit(self._position_s)

    def retranslate(self) -> None:
        self.progress_slider.setAccessibleName(tr("Video playback position slider"))
        self.rewind_15_button.setAccessibleName(tr("Rewind 15 seconds"))
        self.rewind_15_button.setToolTip(tr("Seek 15 seconds back"))
        self.rewind_5_button.setAccessibleName(tr("Rewind 5 seconds"))
        self.rewind_5_button.setToolTip(tr("Seek 5 seconds back"))
        self.forward_5_button.setAccessibleName(tr("Forward 5 seconds"))
        self.forward_5_button.setToolTip(tr("Seek 5 seconds forward"))
        self.forward_15_button.setAccessibleName(tr("Forward 15 seconds"))
        self.forward_15_button.setToolTip(tr("Seek 15 seconds forward"))
        self.position_label.setAccessibleName(tr("Video playback position"))
