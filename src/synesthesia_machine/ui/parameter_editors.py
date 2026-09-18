"""Typed scalar parameter widgets shared by graph nodes and the inspector."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from synesthesia_machine.contracts import ColorValue, NumericMatrix, PortType
from synesthesia_machine.graph import LiteralValue
from synesthesia_machine.nodes import ParameterEditorHint, ParameterSpec
from synesthesia_machine.ui.theme import DEFAULT_THEME, Theme
from synesthesia_machine.ui.tooltips import format_tooltip
from synesthesia_machine.ui.translations import tr, trf
from synesthesia_machine.ui.view_models import ParameterViewModel

type ParameterChanged = Callable[[LiteralValue], None]


@dataclass(frozen=True, slots=True)
class SiblingContext:
    """Snapshot of a node's parameters for sibling-aware editors.

    Bundles the three mappings that would otherwise travel through
    ``create_parameter_editor`` as parallel dicts: values drive
    cross-parameter bounds, connected drives which knobs are inert, and
    specs drive tooltips and duration-bound resolution.
    """

    values: Mapping[str, LiteralValue]
    connected: Mapping[str, bool]
    specs: Mapping[str, ParameterSpec]


def sibling_context_of(parameters: Sequence[ParameterViewModel]) -> SiblingContext:
    """Build the sibling context shared by one node's editors."""

    return SiblingContext(
        values={p.spec.id: p.value for p in parameters},
        connected={p.spec.id: p.connected for p in parameters},
        specs={p.spec.id: p.spec for p in parameters},
    )


class LoopKnob(StrEnum):
    """Which side of the loop-range track owns a knob drag."""

    START = "start"
    END = "end"


class _EditorDragTracker(QObject):
    """Process-wide tracker for in-flight parameter editor drag gestures.

    Surfaces that recreate editor widgets (inspector form rebuilds, scene node
    rebuilds) must not tear down a widget while a drag gesture on it is in
    flight: Python references would keep pointing at deleted C++ objects
    (shiboken "already deleted" on the next use) and queued mouse events or
    signals delivered to the freed widget crash the UI process natively. The
    hazard is most visible during playback, when ~10 Hz telemetry ticks keep
    rebuilding while the user drags a knob.
    """

    activeChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._active_count = 0

    def begin(self) -> None:
        if self._active_count == 0:
            self.activeChanged.emit(True)
        self._active_count += 1

    def end(self) -> None:
        if self._active_count <= 0:
            return
        self._active_count -= 1
        if self._active_count == 0:
            self.activeChanged.emit(False)

    @property
    def is_active(self) -> bool:
        return self._active_count > 0


_editor_drag_tracker: _EditorDragTracker | None = None


def editor_drag_tracker() -> _EditorDragTracker:
    """The process-wide drag tracker (created on first use, app lifetime)."""

    global _editor_drag_tracker
    if _editor_drag_tracker is None:
        _editor_drag_tracker = _EditorDragTracker()
    return _editor_drag_tracker


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


def is_loop_range_anchor(spec: ParameterSpec) -> bool:
    """Whether the spec is the loop-start anchor of the dual-knob editor."""

    return spec.editor_hint is ParameterEditorHint.LOOP_RANGE and spec.id == "loop_start_s"


def is_loop_range_member(spec: ParameterSpec) -> bool:
    """Whether the spec is represented by the loop-range anchor editor."""

    return spec.editor_hint is ParameterEditorHint.LOOP_RANGE and not is_loop_range_anchor(spec)


def create_parameter_editor(
    parameter: ParameterViewModel,
    on_changed: ParameterChanged,
    *,
    compact: bool = False,
    dynamic_choices: Sequence[tuple[str, LiteralValue]] = (),
    siblings: SiblingContext | None = None,
    on_sibling_changed: Callable[[str, LiteralValue], None] | None = None,
    theme: Theme | None = None,
) -> QWidget | None:
    """Create an editor from ParameterSpec metadata without duplicating validation rules.

    Loop-range members (``loop_end_s``) return ``None``: they are represented
    by the anchor's dual-knob editor.
    """

    spec = parameter.spec
    if spec.device_kind is not None:
        editor: QWidget = ChoiceParameterEditor(
            parameter,
            on_changed,
            choices=dynamic_choices,
        )
    elif spec.choices:
        editor = ChoiceParameterEditor(parameter, on_changed)
    elif is_loop_range_anchor(spec):
        editor = LoopRangeParameterEditor(
            parameter,
            on_changed,
            siblings=siblings,
            on_sibling_changed=on_sibling_changed,
            compact=compact,
            theme=theme,
        )
    elif is_loop_range_member(spec):
        return None
    elif spec.editor_hint is ParameterEditorHint.TIMESTAMP and spec.value_type is PortType.FLOAT:
        editor = TimestampParameterEditor(
            parameter, on_changed, sibling_values=siblings.values if siblings is not None else None
        )
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
    if not isinstance(editor, LoopRangeParameterEditor):
        editor.setEnabled(not parameter.connected)
    if compact:
        if isinstance(editor, LoopRangeParameterEditor):
            editor.setFixedHeight(round(LoopRangeParameterEditor.TRACK_HEIGHT))
        else:
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
        editor_drag_tracker().begin()
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
            editor_drag_tracker().end()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.isSliderDown():
            # Destruction abandons the in-flight gesture: drop the slider
            # state and unwind the tracker so surfaces resume rebuilding.
            self.setSliderDown(False)
            editor_drag_tracker().end()
        super().closeEvent(event)

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
        if (
            watched is self._owner
            and event.type() is QEvent.Type.Close
            and self._origin_x is not None
        ):
            # The owner is destroyed mid-scrub: abandon the gesture and
            # unwind the tracker before the watched widgets go away.
            self._abort_scrub()
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
            if not self._scrubbing:
                editor_drag_tracker().begin()
            self._scrubbing = True
            self._line_editor.setCursor(Qt.CursorShape.SizeHorCursor)
            steps = round(delta_x / self._PIXELS_PER_STEP)
            self._write_value(self._origin_value + steps * self._step)
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._finish_scrub(event):
            return True
        return super().eventFilter(watched, event)

    def _abort_scrub(self) -> None:
        self._origin_x = None
        if self._scrubbing:
            self._scrubbing = False
            editor_drag_tracker().end()
            self._line_editor.unsetCursor()

    def _finish_scrub(self, event: QMouseEvent) -> bool:
        if self._origin_x is None:
            return False
        was_scrubbing = self._scrubbing
        self._origin_x = None
        self._scrubbing = False
        if not was_scrubbing:
            return False
        editor_drag_tracker().end()
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
_LOOP_RANGE_TRACK_PAD = 8.0


def format_timestamp(value: float) -> str:
    """Render seconds as ``HH:MM:SS[.cS]`` (centiseconds only when nonzero)."""

    centiseconds = max(0, round(max(0.0, value) * 100.0))
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
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
    resolver bounds them to the video's duration, never more and never
    less); the field accepts typed timestamps such as ``00:01:30``. Cross-
    parameter rules (the loop start stops at the loop end, a positive loop
    end stops at the loop start) come from ``sibling_values`` and are
    re-evaluated by the inspector on every change.

    While the video's duration is still unknown the editor is inert: the
    slider and field are disabled, because any position on a substitute
    scale is almost certainly outside the real video's range. The editor
    re-projects with the real bounds as soon as the engine publishes the
    duration.
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
        self._bounds_fallback = spec.maximum is None
        self._maximum = (
            float(spec.maximum) if spec.maximum is not None else _TIMESTAMP_FALLBACK_MAXIMUM
        )
        # While the video's duration is still unknown the editor is inert:
        # a position on the substitute scale has no meaning against the real
        # video (it would persist a value such as 1500s into a 90s video),
        # so the slider and field are disabled until the engine publishes
        # the duration and this editor re-projects with the real bounds.
        self._pending_drag_commit = False
        self._drag_anchor = self._minimum
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
        # The position math below divides by ``_STEPS``; without an explicit
        # range, QSlider's default 0..99 range caps every value at under 1%
        # of the parameter's range.
        self.slider.setRange(0, self._STEPS if self._maximum > self._minimum else 0)
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
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.timestamp_field.editingFinished.connect(self._commit)

        if self._bounds_fallback:
            # The value stays visible (programmatic updates still land); only
            # user interaction on the substitute scale is impossible.
            self.slider.setEnabled(False)
            self.timestamp_field.setEnabled(False)

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
            # A programmatic (non-drag) slider change commits on the current
            # scale, drag or not.
            self._pending_drag_commit = False
            self._schedule_commit()

    @Slot()
    def _on_slider_pressed(self) -> None:
        # Remember the displayed value when the drag starts so a drag on the
        # fallback scale can snap the display back without committing.
        self._drag_anchor = self._value_for_position()

    @Slot()
    def _on_slider_released(self) -> None:
        self._pending_drag_commit = True
        self._schedule_commit()

    @Slot()
    def _schedule_commit(self) -> None:
        self._commit_timer.start(0)

    @Slot()
    def _commit(self) -> None:
        # A typed timestamp is an explicit user value: honor it even on the
        # fallback scale (the engine reports it clearly if it is out of range).
        self._pending_drag_commit = False
        if self.graphicsProxyWidget() is not None:
            self._schedule_commit()
            return
        self._flush_commit()

    @Slot()
    def _flush_commit(self) -> None:
        if self._pending_drag_commit and self._bounds_fallback:
            # The drag happened on the fallback scale (the video's duration is
            # still unknown): persist nothing and snap the display back to the
            # pre-drag value so it cannot desync from the document.
            self._pending_drag_commit = False
            self._set_value(self._drag_anchor, commit=False)
            return
        self._pending_drag_commit = False
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


class _LoopRangeTrack(QWidget):
    """Painted range track: groove with an end triangle above and a start
    triangle below, both tips touching the groove for precise knob reading."""

    def __init__(
        self,
        parent: LoopRangeParameterEditor,
        theme: Theme,
    ) -> None:
        super().__init__(parent)
        self._owner = parent
        self._theme = theme
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event: QEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = float(self.width())
        centre_y = self.height() / 2.0
        pad = _LOOP_RANGE_TRACK_PAD
        groove = QRectF(pad, centre_y - 2.0, width - 2 * pad, 4.0)
        painter.setPen(QPen(self._theme.color("border")))
        painter.setBrush(QBrush(self._theme.color("canvas")))
        painter.drawRoundedRect(groove, 2.0, 2.0)
        owner = self._owner
        start_x = owner.x_for_position(owner.start_position())
        end_x = owner.x_for_position(owner.end_position())
        if start_x < end_x:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._theme.color("accent")))
            painter.drawRoundedRect(QRectF(start_x, centre_y - 2.0, end_x - start_x, 4.0), 2.0, 2.0)
        self._draw_knob(painter, start_x, centre_y, up=True)
        self._draw_knob(painter, end_x, centre_y, up=False)

    def _draw_knob(self, painter: QPainter, x: float, centre_y: float, *, up: bool) -> None:
        owner = self._owner
        dragging = owner.dragged_knob() is (LoopKnob.START if up else LoopKnob.END)
        fill = self._theme.color("selection") if dragging else self._theme.color("text")
        disabled = owner.knob_disabled(LoopKnob.START if up else LoopKnob.END)
        if disabled:
            fill = self._theme.color("disabled")
        border = self._theme.color("border") if disabled else self._theme.color("accent")
        gap = 2.0  # half the groove thickness: the tip touches the groove.
        tip_y = centre_y + gap if up else centre_y - gap
        base_y = tip_y + (12.0 if up else -12.0)
        path = QPainterPath()
        path.moveTo(x, tip_y)
        path.lineTo(x - 7.0, base_y)
        path.lineTo(x + 7.0, base_y)
        path.closeSubpath()
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self._owner.knob_pressed(event.position().x(), event.position().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._owner.dragged_knob() is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._owner.knob_moved(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self._owner.dragged_knob() is not None:
            self._owner.knob_released()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LoopRangeParameterEditor(QWidget):
    """Dual-knob range editor for the Load Video loop region.

    One full-width slider carries the whole region: the down-pointing
    triangle above the groove is the loop end, the up-pointing triangle
    below it is the loop start. In the inspector, timestamp fields below the
    slider give precise placement (the slider stays full width). A zero
    loop end means "until the end of the video" and sits at the far right
    of the slider.
    """

    _STEPS = 10_000
    TRACK_HEIGHT = 44.0

    def __init__(
        self,
        parameter: ParameterViewModel,
        on_changed: ParameterChanged,
        *,
        siblings: SiblingContext | None = None,
        on_sibling_changed: Callable[[str, LiteralValue], None] | None = None,
        compact: bool = False,
        theme: Theme | None = None,
    ) -> None:
        super().__init__()
        self._on_changed = on_changed
        self._on_sibling_changed = on_sibling_changed
        self._spec = parameter.spec
        self._theme = theme or DEFAULT_THEME
        if siblings is None:
            siblings = SiblingContext(values={}, connected={}, specs={})
        sibling_values = dict(siblings.values)
        connected = dict(siblings.connected)
        self._sibling_specs = dict(siblings.specs)
        self._maximum = (
            float(self._spec.maximum)
            if self._spec.maximum is not None
            else _TIMESTAMP_FALLBACK_MAXIMUM
        )
        start_value = parameter.value
        end_value = sibling_values.get("loop_end_s")
        self._start_s = float(start_value) if isinstance(start_value, float) else 0.0
        self._end_s = float(end_value) if isinstance(end_value, float) else 0.0
        self._start_connected = parameter.connected or bool(connected.get("loop_start_s"))
        self._end_connected = bool(connected.get("loop_end_s"))
        self._dragged_knob: LoopKnob | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0 if compact else 6)
        self.track = _LoopRangeTrack(self, self._theme)
        self.track.setFixedHeight(round(self.TRACK_HEIGHT))
        self.track.setObjectName(f"parameter_{self._spec.id}_track")
        layout.addWidget(self.track)

        self.start_field: QLineEdit | None = None
        self.end_field: QLineEdit | None = None
        self.compact_label: QLabel | None = None
        if not compact:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(6)
            self.start_field = self._make_field("loop_start_s")
            self.end_field = self._make_field("loop_end_s")
            start_label = QLabel(self)
            end_label = QLabel(self)
            grid.addWidget(start_label, 0, 0)
            grid.addWidget(self.start_field, 0, 1)
            grid.addWidget(end_label, 1, 0)
            grid.addWidget(self.end_field, 1, 1)
            grid.setColumnStretch(1, 1)
            layout.addLayout(grid)
            self._start_label = start_label
            self._end_label = end_label
            self.start_field.editingFinished.connect(self._flush_start_field)
            self.end_field.editingFinished.connect(self._flush_end_field)
        else:
            # Canvas: the labelled field pair is too tall for the two-row
            # parameter band, so the region reads out as one read-only line
            # under the track (track 44px + label 10px fits the 54px band).
            # The knobs remain the canvas editing surface.
            self.compact_label = QLabel(self)
            self.compact_label.setObjectName(f"parameter_{self._spec.id}_compact_value")
            self.compact_label.setFixedHeight(10)
            label_font = QFont(self._theme.body_font())
            label_font.setPointSize(7)
            self.compact_label.setFont(label_font)
            self.compact_label.setStyleSheet(f"color: {self._theme.color('muted_text').name()};")
            layout.addWidget(self.compact_label)

        self._bounds_fallback = self._spec.maximum is None
        if self._bounds_fallback:
            # The video's duration is unknown: the track spans a substitute
            # scale on which almost any knob position would be invalid, so
            # the whole range surface is inert until the editor re-projects
            # with the duration the engine published.
            self.track.setEnabled(False)
            if self.start_field is not None:
                self.start_field.setEnabled(False)
            if self.end_field is not None:
                self.end_field.setEnabled(False)

        self._retranslate_contents()
        self._sync_display(commit=False)

    # -- public API -------------------------------------------------------

    def start_value(self) -> float:
        if self.start_field is not None:
            parsed = parse_timestamp(self.start_field.text())
            if parsed is not None:
                return parsed
        return self._start_s

    def end_value(self) -> float:
        if self.end_field is not None:
            parsed = parse_timestamp(self.end_field.text())
            if parsed is not None:
                return parsed
        return self._end_s

    def maximum(self) -> float:
        return self._maximum

    def set_start(self, value: float, *, commit: bool = True) -> None:
        clamped = min(self._maximum, max(0.0, value)) if self._maximum > 0.0 else 0.0
        if self._end_s > 0.0:
            # A positive end is a concrete boundary: the start must stay
            # strictly before it (the validator rejects start >= end).
            clamped = min(
                clamped,
                self._value_for_position(max(0, self._end_position() - 1), is_end=False),
            )
        self._start_s = clamped
        self._sync_display(commit=False)
        if commit:
            self._commit_start()

    def set_end(self, value: float, *, commit: bool = True) -> None:
        clamped = min(self._maximum, max(0.0, value)) if self._maximum > 0.0 else 0.0
        if clamped > 0.0:
            floor = self._min_positive_end_value()
            if floor > 0.0 and clamped < floor:
                clamped = floor
        self._end_s = clamped
        self._sync_display(commit=False)
        if commit:
            self._commit_end()

    def retranslate(self) -> None:
        self._retranslate_contents()

    # -- public surface used by the painted track -------------------------

    def x_for_position(self, position: int) -> float:
        return self._x_for_position(position)

    def start_position(self) -> int:
        return self._start_position()

    def end_position(self) -> int:
        return self._end_position()

    def knob_disabled(self, knob: LoopKnob) -> bool:
        return self._knob_disabled(knob)

    def dragged_knob(self) -> LoopKnob | None:
        return self._dragged_knob

    def knob_pressed(self, x: float, y: float) -> None:
        if self._bounds_fallback:
            # The duration is unknown: the track is inert, so a gesture
            # (including one routed here from the canvas view) cannot move a
            # knob on the substitute scale.
            return
        self._track_pressed(x, y)

    def knob_moved(self, x: float) -> None:
        self._track_moved(x)

    def knob_released(self) -> None:
        self._track_released()

    # -- value/position mapping -------------------------------------------

    def _position_for_value(self, value: float, *, is_end: bool) -> int:
        if self._maximum <= 0.0:
            return 0
        if is_end and value <= 0.0:
            return self._STEPS
        return round(min(self._STEPS, value * self._STEPS / self._maximum))

    def _value_for_position(self, position: int, *, is_end: bool) -> float:
        if self._maximum <= 0.0:
            return 0.0
        if is_end and position >= self._STEPS:
            return 0.0
        return position / self._STEPS * self._maximum

    def _start_position(self) -> int:
        return self._position_for_value(self._start_s, is_end=False)

    def _end_position(self) -> int:
        return self._position_for_value(self._end_s, is_end=True)

    def _min_positive_end_value(self) -> float:
        if self._maximum <= 0.0:
            return 0.0
        min_position = self._start_position() + 1
        if min_position >= self._STEPS:
            return 0.0
        return min_position / self._STEPS * self._maximum

    def _x_for_position(self, position: int) -> float:
        pad = _LOOP_RANGE_TRACK_PAD
        usable = max(1.0, self.track.width() - 2 * pad)
        return pad + position / self._STEPS * usable

    def _position_for_x(self, x: float) -> int:
        pad = _LOOP_RANGE_TRACK_PAD
        usable = max(1.0, self.track.width() - 2 * pad)
        ratio = max(0.0, min(1.0, (x - pad) / usable))
        return round(ratio * self._STEPS)

    # -- knob interaction --------------------------------------------------

    def _knob_disabled(self, knob: LoopKnob) -> bool:
        return self._start_connected if knob is LoopKnob.START else self._end_connected

    def _track_pressed(self, x: float, y: float) -> None:
        if self._maximum <= 0.0 or self._dragged_knob is not None:
            # A gesture is already in flight (e.g. the canvas view began it):
            # never stack a second grab/tracker begin on top of it.
            return
        # Each lane of the track owns exactly one knob: the groove divides
        # the track into the end knob's lane (above, its triangle points
        # down onto the groove) and the start knob's lane (below). An
        # x-only symmetric window let the start knob steal a press aimed
        # at the end knob whenever the two sat close together, as they do
        # for any short loop region, so the lane decides the grab and the
        # x only decides whether the knob is jumped to the clicked point.
        knob = LoopKnob.END if y < self.TRACK_HEIGHT / 2.0 else LoopKnob.START
        if self._knob_disabled(knob):
            # A connected parameter's knob is inert: the press starts no
            # drag and never steals across the groove into the other lane.
            return
        self._dragged_knob = knob
        editor_drag_tracker().begin()
        if self.graphicsProxyWidget() is None:
            # Inspector context: the widget-level grab tracks the pointer.
            # Canvas context: the editor sits inside a QGraphicsProxyWidget,
            # where a windowless-widget grab is unreliable (offscreen lacks
            # grab support entirely; Wayland/X11 routing depends on the
            # proxy's geometry sync). The canvas view owns those drags via
            # its own mouse events, which reach the view on every platform.
            self.track.grabMouse()
        position = self._position_for_x(x)
        if knob is LoopKnob.START:
            self._apply_start_position(position, commit=False)
        else:
            self._apply_end_position(position, commit=False)

    def _track_moved(self, x: float) -> None:
        knob = self._dragged_knob
        if knob is None:
            return
        position = self._position_for_x(x)
        if knob is LoopKnob.START:
            self._apply_start_position(position, commit=False)
        else:
            self._apply_end_position(position, commit=False)

    def _track_released(self) -> None:
        knob = self._dragged_knob
        self._dragged_knob = None
        if knob is None:
            return
        if self.graphicsProxyWidget() is None:
            self.track.releaseMouse()
        # Commit while the drag tracker is still active: the commit fires a
        # session change, and both surfaces defer editor rebuilds only while
        # the tracker says a drag is in flight. Ending the tracker first
        # let the scene rebuild the very editor whose release Qt was still
        # dispatching (use-after-free crash). The tracker is dropped only
        # after the commit has completed.
        if knob is LoopKnob.START:
            self._commit_start()
        else:
            self._commit_end()
        editor_drag_tracker().end()
        self.track.update()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._dragged_knob is not None:
            # Destruction abandons the in-flight gesture: release the grab
            # and unwind the tracker so surfaces resume rebuilding.
            self._dragged_knob = None
            if self.graphicsProxyWidget() is None:
                self.track.releaseMouse()
            editor_drag_tracker().end()
        super().closeEvent(event)

    def _apply_start_position(self, position: int, *, commit: bool) -> None:
        # The start knob cannot run into the end knob: a positive end is a
        # concrete boundary, while 0.0 (far right) still allows the full range.
        limit = self._end_position() - 1 if self._end_s > 0.0 else self._STEPS
        position = max(0, min(position, limit))
        self._start_s = self._value_for_position(position, is_end=False)
        self._sync_display(commit=False)
        if commit:
            self._commit_start()

    def _apply_end_position(self, position: int, *, commit: bool) -> None:
        # A positive loop end must stay strictly after a positive start;
        # the far right (position _STEPS) means 0.0, "until the end".
        if position < self._STEPS and self._start_s > 0.0:
            position = max(position, self._start_position() + 1)
        position = min(position, self._STEPS)
        self._end_s = self._value_for_position(position, is_end=True)
        self._sync_display(commit=False)
        if commit:
            self._commit_end()

    # -- display and commits ----------------------------------------------

    def _displayed_end(self) -> float:
        # The sentinel 0.0 means "until the source ends". When the engine
        # published the duration, show the real end of the video instead of
        # a misleading 00:00:00; on the unbounded substitute scale the
        # fallback maximum is not a real duration, so the sentinel is shown
        # raw there.
        if self._end_s == 0.0 and not self._bounds_fallback and self._maximum > 0.0:
            return self._maximum
        return self._end_s

    def _sync_display(self, *, commit: bool) -> None:
        del commit
        if self.start_field is not None:
            blocked = self.start_field.blockSignals(True)
            self.start_field.setText(format_timestamp(self._start_s))
            self.start_field.blockSignals(blocked)
        if self.end_field is not None:
            blocked = self.end_field.blockSignals(True)
            self.end_field.setText(format_timestamp(self._displayed_end()))
            self.end_field.blockSignals(blocked)
        if self.compact_label is not None:
            self.compact_label.setText(self.compact_value_text())
        self.track.update()

    def compact_value_text(self) -> str:
        """One-line ``start → end`` readout shown under the canvas track."""

        return f"{format_timestamp(self._start_s)} \u2192 {format_timestamp(self._displayed_end())}"

    def _flush_start_field(self) -> None:
        field = self.start_field
        if field is None:
            return
        parsed = parse_timestamp(field.text())
        if parsed is None:
            self._sync_display(commit=False)
            return
        self.set_start(parsed)

    def _flush_end_field(self) -> None:
        field = self.end_field
        if field is None:
            return
        parsed = parse_timestamp(field.text())
        if parsed is None:
            self._sync_display(commit=False)
            return
        if self._end_s == 0.0 and parsed == self._displayed_end():
            # A focus-out without an edit: the resolved duration is only a
            # display of the 0.0 sentinel and must not be committed as a
            # concrete end, or swapping in a longer video would stop the
            # loop short of the new end.
            return
        self.set_end(parsed)

    def _commit_start(self) -> None:
        sanitized = self._spec.sanitize_value(self._start_s)
        if not isinstance(sanitized, float):
            raise TypeError("Loop start sanitization produced a non-float value")
        self._on_changed(sanitized)

    def _commit_end(self) -> None:
        if self._on_sibling_changed is None:
            return
        clamped = min(self._maximum, max(0.0, self._end_s)) if self._maximum > 0.0 else 0.0
        self._on_sibling_changed("loop_end_s", clamped)

    def _make_field(self, parameter_id: str) -> QLineEdit:
        field = QLineEdit(self)
        field.setObjectName(f"parameter_{parameter_id}_timestamp")
        field.setPlaceholderText("HH:MM:SS")
        field.setMinimumWidth(86)
        field.setMaximumWidth(150)
        field.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        field.setProperty("parameterValue", True)
        field.setProperty("parameterValueInput", True)
        return field

    def _retranslate_contents(self) -> None:
        self.track.setAccessibleName(tr("Loop range slider"))
        start_help = parameter_tooltip(self._spec)
        end_spec = self._sibling_specs.get("loop_end_s")
        end_help = parameter_tooltip(end_spec) if end_spec is not None else start_help
        self.track.setToolTip(format_tooltip(start_help))
        if self.start_field is not None and self.end_field is not None:
            self._start_label.setText(tr("Start"))
            self._end_label.setText(tr("End"))
            self.start_field.setAccessibleName(tr("Loop start value"))
            self.end_field.setAccessibleName(tr("Loop end value"))
            self.start_field.setToolTip(format_tooltip(start_help))
            self.end_field.setToolTip(format_tooltip(end_help))


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
