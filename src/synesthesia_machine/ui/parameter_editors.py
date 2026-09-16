"""Typed scalar parameter widgets shared by graph nodes and the inspector."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import cast

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)

from synesthesia_machine.contracts import ColorValue, NumericMatrix, PortType
from synesthesia_machine.graph import LiteralValue
from synesthesia_machine.nodes import ParameterEditorHint, ParameterSpec
from synesthesia_machine.ui.tooltips import format_tooltip
from synesthesia_machine.ui.translations import tr, trf
from synesthesia_machine.ui.view_models import ParameterViewModel

type ParameterChanged = Callable[[LiteralValue], None]


def _color_dialog_parent(editor: QWidget) -> QWidget | None:
    active_window = QApplication.activeWindow()
    return None if active_window is editor else active_window


def _float_editor_decimals(spec: ParameterSpec) -> int:
    magnitudes = tuple(
        abs(value)
        for value in (spec.default, spec.minimum, spec.maximum)
        if isinstance(value, float) and math.isfinite(value) and value != 0.0
    )
    if not magnitudes:
        return 6
    required = math.ceil(-math.log10(min(magnitudes)))
    return min(12, max(6, required))


def create_parameter_editor(
    parameter: ParameterViewModel,
    on_changed: ParameterChanged,
    *,
    compact: bool = False,
    dynamic_choices: Sequence[tuple[str, LiteralValue]] = (),
    sibling_values: Mapping[str, LiteralValue] | None = None,
) -> QWidget:
    """Create an editor from ParameterSpec metadata without duplicating validation rules."""

    spec = parameter.spec
    if spec.device_kind is not None:
        editor: QWidget = ChoiceParameterEditor(
            parameter,
            on_changed,
            choices=dynamic_choices,
        )
    elif spec.choices:
        editor = ChoiceParameterEditor(parameter, on_changed)
    elif spec.editor_hint is ParameterEditorHint.TIMESTAMP and spec.value_type is PortType.FLOAT:
        editor = TimestampParameterEditor(parameter, on_changed, sibling_values=sibling_values)
    elif spec.value_type is PortType.FLOAT and _uses_slider(parameter):
        editor = FloatRangeParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.FLOAT:
        editor = FloatParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.INT and _uses_slider(parameter):
        editor = IntRangeParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.INT:
        editor = IntParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.BOOL:
        editor = BoolParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.STRING and spec.id == "file_path":
        editor = FilePathParameterEditor(parameter, on_changed, compact=compact)
    elif spec.value_type is PortType.STRING:
        editor = StringParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.COLOR:
        editor = ColorParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.MATRIX:
        editor = MatrixParameterEditor(parameter, on_changed)
    else:
        raise ValueError(f"No scalar editor is available for {spec.value_type.value}")

    editor.setObjectName(f"parameter_{spec.id}")
    editor.setAccessibleName(tr(spec.label))
    help_text = parameter_tooltip(spec)
    editor.setToolTip(format_tooltip(help_text))
    editor.setStatusTip(help_text)
    editor.setEnabled(not parameter.connected)
    if compact:
        editor.setMaximumHeight(23)
    return editor


def parameter_tooltip(spec: ParameterSpec) -> str:
    """Build concise inline help even when a node has no authored help text."""

    if spec.help_text.strip():
        return tr(spec.help_text.strip())
    label = tr(spec.label).casefold()
    if spec.value_type is PortType.BOOL:
        summary = trf("Turns {label} on or off.", label=label)
    elif spec.id == "file_path":
        summary = tr("Selects the video file this source decodes.")
    elif spec.choices:
        summary = trf("Selects the {label} setting.", label=label)
    else:
        summary = trf("Sets {label}.", label=label)
    details: list[str] = []
    if spec.minimum is not None and spec.maximum is not None:
        details.append(
            trf("Range: {minimum} to {maximum}.", minimum=spec.minimum, maximum=spec.maximum)
        )
    elif spec.minimum is not None:
        details.append(trf("Minimum: {minimum}.", minimum=spec.minimum))
    elif spec.maximum is not None:
        details.append(trf("Maximum: {maximum}.", maximum=spec.maximum))
    if spec.choices and len(spec.choices) <= 8:
        details.append(
            trf("Options: {options}.", options=", ".join(str(value) for value in spec.choices))
        )
    if spec.step is not None:
        details.append(trf("Step: {step}.", step=spec.step))
    if spec.connectable:
        details.append(tr("A connected input overrides this value."))
    return " ".join((summary, *details))


def _uses_slider(parameter: ParameterViewModel) -> bool:
    spec = parameter.spec
    return (
        spec.editor_hint is ParameterEditorHint.SLIDER
        and spec.minimum is not None
        and spec.maximum is not None
    )


class DirectDragSlider(QSlider):
    """Slider whose groove supports direct, continuous click-and-drag input."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setSliderDown(True)
        self._set_position_from_pointer(event.position())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.isSliderDown() and event.buttons() & Qt.MouseButton.LeftButton:
            self._set_position_from_pointer(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.isSliderDown():
            self._set_position_from_pointer(event.position())
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_position_from_pointer(self, position: QPointF) -> None:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        if self.orientation() is Qt.Orientation.Horizontal:
            slider_minimum = groove.x()
            slider_maximum = groove.right() - handle.width() + 1
            pointer = round(position.x() - handle.width() / 2.0)
        else:
            slider_minimum = groove.y()
            slider_maximum = groove.bottom() - handle.height() + 1
            pointer = round(position.y() - handle.height() / 2.0)
        available = max(0, slider_maximum - slider_minimum)
        value = QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            pointer - slider_minimum,
            available,
            option.upsideDown,
        )
        self.setSliderPosition(value)


class _NumericScrubController(QObject):
    """Translate a horizontal drag over a numeric field into value steps."""

    finished = Signal()
    _PIXELS_PER_STEP = 2.0

    def __init__(
        self,
        line_editor: QLineEdit,
        read_value: Callable[[], float],
        write_value: Callable[[float], None],
        owner: QWidget,
    ) -> None:
        super().__init__(owner)
        self._line_editor = line_editor
        self._owner = owner
        self._read_value = read_value
        self._write_value = write_value
        self._step = 1.0
        self._origin_x: float | None = None
        self._origin_value = 0.0
        self._scrubbing = False
        self._line_editor.installEventFilter(self)
        self._owner.installEventFilter(self)

    @property
    def is_scrubbing(self) -> bool:
        return self._scrubbing

    def set_step(self, step: float) -> None:
        if step <= 0.0:
            raise ValueError("Numeric scrub step must be positive")
        self._step = step

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)
        if watched is self._owner and event.type() == QEvent.Type.MouseButtonRelease:
            return self._finish_scrub(event) or super().eventFilter(watched, event)
        if watched is not self._line_editor:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() is Qt.MouseButton.LeftButton:
                self._origin_x = event.globalPosition().x()
                self._origin_value = self._read_value()
                self._scrubbing = False
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseMove:
            if self._origin_x is None or not event.buttons() & Qt.MouseButton.LeftButton:
                return super().eventFilter(watched, event)
            delta_x = event.globalPosition().x() - self._origin_x
            if not self._scrubbing and abs(delta_x) < QApplication.startDragDistance():
                return super().eventFilter(watched, event)
            self._scrubbing = True
            self._line_editor.setCursor(Qt.CursorShape.SizeHorCursor)
            steps = round(delta_x / self._PIXELS_PER_STEP)
            self._write_value(self._origin_value + steps * self._step)
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._finish_scrub(event):
            return True
        return super().eventFilter(watched, event)

    def _finish_scrub(self, event: QMouseEvent) -> bool:
        if self._origin_x is None:
            return False
        was_scrubbing = self._scrubbing
        self._origin_x = None
        self._scrubbing = False
        if not was_scrubbing:
            return False
        self._line_editor.unsetCursor()
        self.finished.emit()
        event.accept()
        return True


class ScrubbableDoubleSpinBox(QDoubleSpinBox):
    """Double spin box that also supports Premiere-style horizontal scrubbing."""

    scrubFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scrub_controller = _NumericScrubController(
            self.lineEdit(),
            lambda: float(self.value()),
            self.setValue,
            self,
        )
        self._scrub_controller.set_step(0.01)
        self._scrub_controller.finished.connect(lambda: self.scrubFinished.emit())

    @property
    def is_scrubbing(self) -> bool:
        return self._scrub_controller.is_scrubbing

    def set_scrub_step(self, step: float) -> None:
        self._scrub_controller.set_step(step)


class ScrubbableSpinBox(QSpinBox):
    """Integer spin box that also supports horizontal scrubbing."""

    scrubFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scrub_controller = _NumericScrubController(
            self.lineEdit(),
            lambda: float(self.value()),
            lambda value: self.setValue(round(value)),
            self,
        )
        self._scrub_controller.finished.connect(lambda: self.scrubFinished.emit())

    @property
    def is_scrubbing(self) -> bool:
        return self._scrub_controller.is_scrubbing

    def set_scrub_step(self, step: float) -> None:
        self._scrub_controller.set_step(step)


class _RangeParameterEditor(QWidget):
    """Shared slider and editable value surface for finite metadata bounds."""

    def __init__(
        self,
        parameter: ParameterViewModel,
        on_changed: ParameterChanged,
        value_editor: ScrubbableSpinBox | ScrubbableDoubleSpinBox,
    ) -> None:
        super().__init__()
        self._on_changed = on_changed
        self._spec = parameter.spec
        self.slider = DirectDragSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName(f"parameter_{parameter.spec.id}_slider")
        self.slider.setAccessibleName(trf("{label} slider", label=tr(parameter.spec.label)))
        self.value_editor = value_editor
        self.value_editor.setParent(self)
        self.value_editor.setObjectName(f"parameter_{parameter.spec.id}_value")
        self.value_editor.setAccessibleName(trf("{label} value", label=tr(parameter.spec.label)))
        self.value_editor.setProperty("parameterValue", True)
        self.value_editor.setProperty("parameterValueInput", True)
        self.value_editor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_editor.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.value_editor.setKeyboardTracking(False)
        self.value_editor.setMinimumWidth(68)
        self.value_editor.setMaximumWidth(92)
        # Kept as a compatibility alias for integrations that used the old read-only label.
        self.value_label = self.value_editor
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_editor)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit)
        self.slider.valueChanged.connect(self._value_changed)
        self.slider.sliderReleased.connect(self._schedule_commit)
        self.value_editor.valueChanged.connect(self._field_value_changed)
        self.value_editor.scrubFinished.connect(self._schedule_commit)

    @Slot(int)
    def _value_changed(self, position: int) -> None:
        del position
        self._update_value_editor()
        if not self.slider.isSliderDown():
            self._schedule_commit()

    @Slot()
    def _field_value_changed(self) -> None:
        self._update_slider_from_editor()
        if not self.value_editor.is_scrubbing:
            self._schedule_commit()

    @Slot()
    def _schedule_commit(self) -> None:
        self._commit_timer.start(0)

    def _update_value_editor(self) -> None:
        raise NotImplementedError

    def _update_slider_from_editor(self) -> None:
        raise NotImplementedError

    def _commit(self) -> None:
        raise NotImplementedError


class FloatRangeParameterEditor(_RangeParameterEditor):
    """Linear high-resolution slider for a bounded floating-point parameter."""

    _STEPS = 10_000

    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        spec = parameter.spec
        assert spec.minimum is not None and spec.maximum is not None
        self._minimum = float(spec.minimum)
        self._maximum = float(spec.maximum)
        value_editor = ScrubbableDoubleSpinBox()
        value_editor.setDecimals(_float_editor_decimals(spec))
        value_editor.setRange(self._minimum, self._maximum)
        value_editor.set_scrub_step(max((self._maximum - self._minimum) / 100.0, 1e-6))
        super().__init__(parameter, on_changed, value_editor)
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._STEPS if self._maximum > self._minimum else 0)
        value = float(parameter.value) if isinstance(parameter.value, float) else self._minimum
        self.slider.setValue(self._position_for_value(value))
        self.slider.blockSignals(False)
        self._set_editor_value(value)

    def minimum(self) -> float:
        return self._minimum

    def maximum(self) -> float:
        return self._maximum

    def value(self) -> float:
        return float(self.value_editor.value())

    def setValue(self, value: float) -> None:
        self.slider.setValue(self._position_for_value(value))

    def _position_for_value(self, value: float) -> int:
        if self._maximum <= self._minimum:
            return 0
        clamped = min(self._maximum, max(self._minimum, value))
        return round((clamped - self._minimum) * self._STEPS / (self._maximum - self._minimum))

    def _value_for_position(self) -> float:
        if self._maximum <= self._minimum:
            return self._minimum
        ratio = self.slider.value() / self._STEPS
        return self._minimum + ratio * (self._maximum - self._minimum)

    def _set_editor_value(self, value: float) -> None:
        blocked = self.value_editor.blockSignals(True)
        cast(QDoubleSpinBox, self.value_editor).setValue(
            min(self._maximum, max(self._minimum, value))
        )
        self.value_editor.blockSignals(blocked)

    def _update_value_editor(self) -> None:
        self._set_editor_value(self._value_for_position())

    def _update_slider_from_editor(self) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(self._position_for_value(self.value()))
        self.slider.blockSignals(blocked)

    @Slot()
    def _commit(self) -> None:
        sanitized = self._spec.sanitize_value(float(self.value()))
        if not isinstance(sanitized, float):
            raise TypeError("Float parameter sanitization produced a non-float value")
        self._set_editor_value(sanitized)
        self._update_slider_from_editor()
        self._on_changed(sanitized)


class IntRangeParameterEditor(_RangeParameterEditor):
    """Native integer slider for a bounded integer parameter."""

    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        spec = parameter.spec
        assert spec.minimum is not None and spec.maximum is not None
        self._minimum = int(spec.minimum)
        self._maximum = int(spec.maximum)
        value_editor = ScrubbableSpinBox()
        value_editor.setRange(self._minimum, self._maximum)
        value_editor.setSingleStep(spec.step or 1)
        super().__init__(parameter, on_changed, value_editor)
        self.slider.blockSignals(True)
        self.slider.setRange(self._minimum, self._maximum)
        self.slider.setSingleStep(spec.step or 1)
        value = (
            int(parameter.value)
            if isinstance(parameter.value, int) and not isinstance(parameter.value, bool)
            else self._minimum
        )
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self._set_editor_value(value)

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return int(self.value_editor.value())

    def setValue(self, value: int) -> None:
        self.slider.setValue(value)

    def _set_editor_value(self, value: int) -> None:
        blocked = self.value_editor.blockSignals(True)
        self.value_editor.setValue(min(self._maximum, max(self._minimum, value)))
        self.value_editor.blockSignals(blocked)

    def _update_value_editor(self) -> None:
        self._set_editor_value(self.slider.value())

    def _update_slider_from_editor(self) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(self.value())
        self.slider.blockSignals(blocked)

    @Slot()
    def _commit(self) -> None:
        sanitized = self._spec.sanitize_value(int(self.value()))
        if not isinstance(sanitized, int) or isinstance(sanitized, bool):
            raise TypeError("Integer parameter sanitization produced a non-integer value")
        self._set_editor_value(sanitized)
        self._update_slider_from_editor()
        self._on_changed(sanitized)


class FloatParameterEditor(ScrubbableDoubleSpinBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        spec = parameter.spec
        self._spec = spec
        minimum = float(spec.minimum) if spec.minimum is not None else -1_000_000_000.0
        maximum = float(spec.maximum) if spec.maximum is not None else 1_000_000_000.0
        self.setDecimals(_float_editor_decimals(spec))
        self.setRange(minimum, maximum)
        if spec.minimum is not None and spec.maximum is not None:
            self.set_scrub_step(max((maximum - minimum) / 100.0, 1e-6))
        self.setKeyboardTracking(False)
        if isinstance(parameter.value, float):
            self.setValue(parameter.value)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self.valueChanged.connect(self._commit)
        self.editingFinished.connect(self._commit)
        self.scrubFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
        if self.is_scrubbing:
            return
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        sanitized = self._spec.sanitize_value(float(self.value()))
        if not isinstance(sanitized, float):
            raise TypeError("Float parameter sanitization produced a non-float value")
        blocked = self.blockSignals(True)
        self.setValue(sanitized)
        self.blockSignals(blocked)
        self._on_changed(sanitized)


class IntParameterEditor(ScrubbableSpinBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        spec = parameter.spec
        self._spec = spec
        minimum = int(spec.minimum) if spec.minimum is not None else -2_147_483_648
        maximum = int(spec.maximum) if spec.maximum is not None else 2_147_483_647
        self.setRange(minimum, maximum)
        self.setSingleStep(spec.step or 1)
        self.setKeyboardTracking(False)
        if isinstance(parameter.value, int) and not isinstance(parameter.value, bool):
            self.setValue(parameter.value)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self.valueChanged.connect(self._commit)
        self.editingFinished.connect(self._commit)
        self.scrubFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
        if self.is_scrubbing:
            return
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        sanitized = self._spec.sanitize_value(int(self.value()))
        if not isinstance(sanitized, int) or isinstance(sanitized, bool):
            raise TypeError("Integer parameter sanitization produced a non-integer value")
        blocked = self.blockSignals(True)
        self.setValue(sanitized)
        self.blockSignals(blocked)
        self._on_changed(sanitized)


_TIMESTAMP_PART = re.compile(r"\d+(\.\d+)?")
_TIMESTAMP_FALLBACK_MAXIMUM = 3600.0


def format_timestamp(value: float) -> str:
    """Render seconds as ``HH:MM:SS[.cS]`` (centiseconds only when nonzero)."""

    centiseconds = max(0, round(max(0.0, value) * 100.0))
    hours, rest = divmod(centiseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, fraction = divmod(rest, 100)
    if fraction:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_timestamp(text: str) -> float | None:
    """Parse ``HH:MM:SS[.cS]``, ``MM:SS[.cS]``, or plain seconds into seconds."""

    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    value = 0.0
    for part in parts:
        part = part.strip()
        if not _TIMESTAMP_PART.fullmatch(part):
            return None
        value = value * 60.0 + float(part)
    if not math.isfinite(value):
        return None
    return value


class TimestampParameterEditor(QWidget):
    """Slider and editable timestamp field for a video-time FLOAT parameter.

    The slider spans the parameter's resolved bounds (the node's editor
    resolver bounds them to the video's duration); the field accepts typed
    timestamps such as ``00:01:30``. Cross-parameter rules (the loop start
    stops at the loop end, a positive loop end stops at the loop start)
    come from ``sibling_values`` and are re-evaluated by the inspector on
    every change.
    """

    _STEPS = 10_000

    def __init__(
        self,
        parameter: ParameterViewModel,
        on_changed: ParameterChanged,
        *,
        sibling_values: Mapping[str, LiteralValue] | None = None,
    ) -> None:
        super().__init__()
        self._on_changed = on_changed
        spec = parameter.spec
        self._spec = spec
        siblings = dict(sibling_values or {})
        self._minimum = float(spec.minimum) if spec.minimum is not None else 0.0
        self._maximum = (
            float(spec.maximum) if spec.maximum is not None else _TIMESTAMP_FALLBACK_MAXIMUM
        )
        start_sibling = siblings.get("loop_start_s")
        end_sibling = siblings.get("loop_end_s")
        self._start_sibling: float | None = None
        if spec.id == "loop_start_s" and isinstance(end_sibling, float) and end_sibling > 0.0:
            self._maximum = min(self._maximum, end_sibling)
        elif spec.id == "loop_end_s" and isinstance(start_sibling, float) and start_sibling > 0.0:
            self._start_sibling = start_sibling
        if self._maximum < self._minimum:
            self._maximum = self._minimum

        self.slider = DirectDragSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName(f"parameter_{spec.id}_slider")
        self.slider.setAccessibleName(trf("{label} slider", label=tr(spec.label)))
        self.timestamp_field = QLineEdit(self)
        self.timestamp_field.setObjectName(f"parameter_{spec.id}_timestamp")
        self.timestamp_field.setAccessibleName(trf("{label} value", label=tr(spec.label)))
        self.timestamp_field.setProperty("parameterValue", True)
        self.timestamp_field.setProperty("parameterValueInput", True)
        self.timestamp_field.setPlaceholderText("HH:MM:SS")
        self.timestamp_field.setMinimumWidth(86)
        self.timestamp_field.setMaximumWidth(110)
        self.timestamp_field.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.timestamp_field)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self.slider.valueChanged.connect(self._update_field_from_slider)
        self.slider.sliderReleased.connect(self._schedule_commit)
        self.timestamp_field.editingFinished.connect(self._commit)

        value = float(parameter.value) if isinstance(parameter.value, float) else self._minimum
        self._set_value(value, commit=False)

    def minimum(self) -> float:
        return self._minimum

    def maximum(self) -> float:
        return self._maximum

    def value(self) -> float:
        parsed = parse_timestamp(self.timestamp_field.text())
        if parsed is not None:
            return self._constrained(parsed)
        return self._value_for_position()

    def setValue(self, value: float) -> None:
        self._set_value(value)

    def _position_for_value(self, value: float) -> int:
        if self._maximum <= self._minimum:
            return 0
        clamped = min(self._maximum, max(self._minimum, value))
        return round((clamped - self._minimum) * self._STEPS / (self._maximum - self._minimum))

    def _value_for_position(self) -> float:
        if self._maximum <= self._minimum:
            return self._minimum
        ratio = self.slider.value() / self._STEPS
        return self._minimum + ratio * (self._maximum - self._minimum)

    def _constrained(self, value: float) -> float:
        clamped = min(self._maximum, max(self._minimum, value))
        # A positive loop end may not sink below a positive loop start; zero
        # still means "until the end of the video".
        if self._start_sibling is not None and 0.0 < clamped < self._start_sibling:
            clamped = self._start_sibling
        return clamped

    def _set_value(self, value: float, *, commit: bool = True) -> None:
        clamped = self._constrained(value)
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(self._position_for_value(clamped))
        self.slider.blockSignals(blocked)
        blocked = self.timestamp_field.blockSignals(True)
        self.timestamp_field.setText(format_timestamp(clamped))
        self.timestamp_field.blockSignals(blocked)
        if commit:
            self._on_changed(self._spec.sanitize_value(clamped))

    @Slot()
    def _update_field_from_slider(self) -> None:
        blocked = self.timestamp_field.blockSignals(True)
        self.timestamp_field.setText(format_timestamp(self._value_for_position()))
        self.timestamp_field.blockSignals(blocked)
        if not self.slider.isSliderDown():
            self._schedule_commit()

    @Slot()
    def _schedule_commit(self) -> None:
        self._commit_timer.start(0)

    @Slot()
    def _commit(self) -> None:
        if self.graphicsProxyWidget() is not None:
            self._schedule_commit()
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        parsed = parse_timestamp(self.timestamp_field.text())
        value = self._value_for_position() if parsed is None else parsed
        clamped = self._constrained(value)
        sanitized = self._spec.sanitize_value(clamped)
        if not isinstance(sanitized, float):
            raise TypeError("Timestamp sanitization produced a non-float value")
        if sanitized != clamped:
            # The static spec (e.g. the resolved video duration) bound the
            # value differently than the sibling-derived editor bounds.
            clamped = sanitized
        self._set_value(clamped, commit=False)
        self._on_changed(sanitized)


class BoolParameterEditor(QCheckBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        self._pending_value = False
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        if isinstance(parameter.value, bool):
            self.setChecked(parameter.value)
        self.toggled.connect(self._commit)

    @Slot(bool)
    def _commit(self, checked: bool) -> None:
        self._pending_value = checked
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        self._on_changed(self._pending_value)


class StringParameterEditor(QLineEdit):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        if isinstance(parameter.value, str):
            self.setText(parameter.value)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self.editingFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        self._on_changed(self.text())


class FilePathParameterEditor(QWidget):
    """Editable media path with an adjacent native file picker."""

    VIDEO_FILTER = "Video files (*.avi *.m4v *.mkv *.mov *.mp4 *.mpeg *.mpg *.webm);;All files (*)"

    def __init__(
        self,
        parameter: ParameterViewModel,
        on_changed: ParameterChanged,
        *,
        compact: bool,
    ) -> None:
        super().__init__()
        self._on_changed = on_changed
        self.path_edit = QLineEdit(self)
        self.path_edit.setObjectName(f"parameter_{parameter.spec.id}_text")
        self.path_edit.setAccessibleName(trf("{label} text", label=tr(parameter.spec.label)))
        if isinstance(parameter.value, str):
            self.path_edit.setText(parameter.value)
        self.browse_button = QPushButton("…" if compact else tr("Browse…"), self)
        self.browse_button.setObjectName(f"parameter_{parameter.spec.id}_browse")
        self.browse_button.setAccessibleName(
            trf("Browse for {label}", label=tr(parameter.spec.label).casefold())
        )
        self.browse_button.setToolTip(tr("Choose a video file"))
        if compact:
            self.browse_button.setFixedWidth(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.path_edit, 1)
        layout.addWidget(self.browse_button)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self.path_edit.editingFinished.connect(self._commit)
        self.browse_button.clicked.connect(self._browse)

    def text(self) -> str:
        return self.path_edit.text()

    def setText(self, value: str) -> None:
        self.path_edit.setText(value)

    @Slot()
    def _commit(self) -> None:
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        self._on_changed(self.path_edit.text())

    @Slot(bool)
    def _browse(self, checked: bool = False) -> None:
        del checked
        active_window = QApplication.activeWindow()
        dialog_parent = active_window if active_window is not None else None
        selected, _selected_filter = QFileDialog.getOpenFileName(
            dialog_parent,
            tr("Choose video file"),
            self.path_edit.text(),
            tr(self.VIDEO_FILTER),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not selected:
            return
        self.path_edit.setText(selected)
        self._commit()


class MatrixParameterEditor(QLineEdit):
    """Compact JSON nested-array editor for immutable numeric matrices."""

    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        if isinstance(parameter.value, NumericMatrix):
            self.setText(json.dumps(parameter.value.rows, separators=(",", ":")))
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self.editingFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        try:
            raw = cast(object, json.loads(self.text()))
            matrix = NumericMatrix(_numeric_matrix_rows(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            self.setStyleSheet("border: 1px solid #c44;")
            return
        self.setStyleSheet("")
        self._on_changed(matrix)


def _numeric_matrix_rows(value: object) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list):
        raise ValueError("matrix must be a nested array")
    rows: list[tuple[float, ...]] = []
    for raw_row in cast(list[object], value):
        if not isinstance(raw_row, list):
            raise ValueError("matrix rows must be arrays")
        row: list[float] = []
        for raw_value in cast(list[object], raw_row):
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError("matrix entries must be numbers")
            try:
                row.append(float(raw_value))
            except OverflowError as error:
                raise ValueError("matrix entries must be finite") from error
        rows.append(tuple(row))
    return tuple(rows)


class ChoiceParameterEditor(QComboBox):
    def __init__(
        self,
        parameter: ParameterViewModel,
        on_changed: ParameterChanged,
        *,
        choices: Sequence[tuple[str, LiteralValue]] = (),
    ) -> None:
        super().__init__()
        self._on_changed = on_changed
        if choices:
            for label, value in choices:
                self.addItem(label, value)
        else:
            for value in parameter.spec.choices:
                self.addItem(str(value), value)
        index = self.findData(parameter.value)
        if index >= 0:
            self.setCurrentIndex(index)
        self.view().setObjectName("parameter_choice_popup")
        self._pending_index = -1
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit_pending)
        self._original_proxy_z: float | None = None
        self.currentIndexChanged.connect(self._selection_changed)

    def showPopup(self) -> None:
        proxy = self.graphicsProxyWidget()
        if proxy is not None:
            self._original_proxy_z = proxy.zValue()
            proxy.setZValue(10_000.0)
        super().showPopup()
        self.view().raise_()

    def hidePopup(self) -> None:
        super().hidePopup()
        proxy = self.graphicsProxyWidget()
        if proxy is not None and self._original_proxy_z is not None:
            proxy.setZValue(self._original_proxy_z)
        self._original_proxy_z = None

    @Slot(int)
    def _selection_changed(self, index: int) -> None:
        if self.graphicsProxyWidget() is None:
            self._commit(index)
            return
        self._pending_index = index
        self._commit_timer.start(0)

    @Slot()
    def _commit_pending(self) -> None:
        self._commit(self._pending_index)

    @Slot(int)
    def _commit(self, index: int) -> None:
        value = self.itemData(index)
        if isinstance(value, (str, int, float, bool, ColorValue)):
            self._on_changed(value)


class ColorParameterEditor(QPushButton):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__(tr("Choose…"))
        self._on_changed = on_changed
        self._value = (
            parameter.value if isinstance(parameter.value, ColorValue) else ColorValue(0, 0, 0)
        )
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._flush_commit)
        self._update_swatch()
        self.clicked.connect(self._choose)

    @Slot(bool)
    def _choose(self, checked: bool = False) -> None:
        del checked
        initial = QColor.fromRgbF(self._value.r, self._value.g, self._value.b, self._value.a)
        # Parenting a modal dialog to a widget embedded by QGraphicsProxyWidget makes Qt embed the
        # dialog itself in the graph scene. On Windows that proxy can expand across the canvas and
        # paint it white. The real application window keeps the colour dialog top-level instead.
        dialog_parent = _color_dialog_parent(self)
        selected = QColorDialog.getColor(
            initial,
            dialog_parent,
            tr("Choose colour"),
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if not selected.isValid():
            return
        self._value = ColorValue(
            selected.redF(), selected.greenF(), selected.blueF(), selected.alphaF()
        )
        self._update_swatch()
        if self.graphicsProxyWidget() is not None:
            self._commit_timer.start(0)
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        self._on_changed(self._value)

    def _update_swatch(self) -> None:
        color = QColor.fromRgbF(self._value.r, self._value.g, self._value.b, self._value.a)
        self.setStyleSheet(f"background-color: {color.name(QColor.NameFormat.HexArgb)};")
