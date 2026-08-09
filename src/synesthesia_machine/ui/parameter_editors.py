"""Typed scalar parameter widgets shared by graph nodes and the inspector."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from PySide6.QtCore import QPointF, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
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
from synesthesia_machine.nodes import ParameterEditorHint
from synesthesia_machine.ui.view_models import ParameterViewModel

type ParameterChanged = Callable[[LiteralValue], None]


def create_parameter_editor(
    parameter: ParameterViewModel,
    on_changed: ParameterChanged,
    *,
    compact: bool = False,
) -> QWidget:
    """Create an editor from ParameterSpec metadata without duplicating validation rules."""

    spec = parameter.spec
    if spec.choices:
        editor: QWidget = ChoiceParameterEditor(parameter, on_changed)
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
    editor.setAccessibleName(spec.label)
    editor.setToolTip(spec.help_text or f"{spec.label} ({spec.value_type.value})")
    editor.setStatusTip(editor.toolTip())
    editor.setEnabled(not parameter.connected)
    if compact:
        editor.setMaximumHeight(23)
    return editor


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


class _RangeParameterEditor(QWidget):
    """Shared slider/value surface for parameters with finite metadata bounds."""

    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        self.slider = DirectDragSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName(f"parameter_{parameter.spec.id}_slider")
        self.slider.setAccessibleName(f"{parameter.spec.label} slider")
        self.value_label = QLabel(self)
        self.value_label.setObjectName(f"parameter_{parameter.spec.id}_value")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setMinimumWidth(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit)
        self.slider.valueChanged.connect(self._value_changed)
        self.slider.sliderReleased.connect(self._schedule_commit)

    @Slot(int)
    def _value_changed(self, position: int) -> None:
        del position
        self._update_value_label()
        if not self.slider.isSliderDown():
            self._schedule_commit()

    @Slot()
    def _schedule_commit(self) -> None:
        self._commit_timer.start(0)

    def _update_value_label(self) -> None:
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
        super().__init__(parameter, on_changed)
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._STEPS if self._maximum > self._minimum else 0)
        value = float(parameter.value) if isinstance(parameter.value, float) else self._minimum
        self.slider.setValue(self._position_for_value(value))
        self.slider.blockSignals(False)
        self._update_value_label()

    def minimum(self) -> float:
        return self._minimum

    def maximum(self) -> float:
        return self._maximum

    def value(self) -> float:
        if self._maximum <= self._minimum:
            return self._minimum
        ratio = self.slider.value() / self._STEPS
        return self._minimum + ratio * (self._maximum - self._minimum)

    def setValue(self, value: float) -> None:
        self.slider.setValue(self._position_for_value(value))

    def _position_for_value(self, value: float) -> int:
        if self._maximum <= self._minimum:
            return 0
        clamped = min(self._maximum, max(self._minimum, value))
        return round((clamped - self._minimum) * self._STEPS / (self._maximum - self._minimum))

    def _update_value_label(self) -> None:
        self.value_label.setText(f"{self.value():.6g}")

    @Slot()
    def _commit(self) -> None:
        self._on_changed(float(self.value()))


class IntRangeParameterEditor(_RangeParameterEditor):
    """Native integer slider for a bounded integer parameter."""

    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        spec = parameter.spec
        assert spec.minimum is not None and spec.maximum is not None
        self._minimum = int(spec.minimum)
        self._maximum = int(spec.maximum)
        super().__init__(parameter, on_changed)
        self.slider.blockSignals(True)
        self.slider.setRange(self._minimum, self._maximum)
        value = (
            int(parameter.value)
            if isinstance(parameter.value, int) and not isinstance(parameter.value, bool)
            else self._minimum
        )
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self._update_value_label()

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value: int) -> None:
        self.slider.setValue(value)

    def _update_value_label(self) -> None:
        self.value_label.setText(str(self.value()))

    @Slot()
    def _commit(self) -> None:
        self._on_changed(int(self.value()))


class FloatParameterEditor(QDoubleSpinBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        spec = parameter.spec
        minimum = float(spec.minimum) if spec.minimum is not None else -1_000_000_000.0
        maximum = float(spec.maximum) if spec.maximum is not None else 1_000_000_000.0
        self.setRange(minimum, maximum)
        self.setDecimals(6)
        self.setKeyboardTracking(False)
        if isinstance(parameter.value, float):
            self.setValue(parameter.value)
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
        self._on_changed(float(self.value()))


class IntParameterEditor(QSpinBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        spec = parameter.spec
        minimum = int(spec.minimum) if spec.minimum is not None else -2_147_483_648
        maximum = int(spec.maximum) if spec.maximum is not None else 2_147_483_647
        self.setRange(minimum, maximum)
        self.setKeyboardTracking(False)
        if isinstance(parameter.value, int) and not isinstance(parameter.value, bool):
            self.setValue(parameter.value)
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
        self._on_changed(int(self.value()))


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
        self.path_edit.setAccessibleName(f"{parameter.spec.label} text")
        if isinstance(parameter.value, str):
            self.path_edit.setText(parameter.value)
        self.browse_button = QPushButton("…" if compact else "Browse…", self)
        self.browse_button.setObjectName(f"parameter_{parameter.spec.id}_browse")
        self.browse_button.setAccessibleName(f"Browse for {parameter.spec.label.casefold()}")
        self.browse_button.setToolTip("Choose a video file")
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
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose video file",
            self.path_edit.text(),
            self.VIDEO_FILTER,
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
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
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
        super().__init__("Choose…")
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
        selected = QColorDialog.getColor(
            initial,
            self,
            "Choose colour",
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
