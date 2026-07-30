"""Typed scalar parameter widgets shared by graph nodes and the inspector."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from synesthesia_machine.contracts import ColorValue, PortType
from synesthesia_machine.graph import LiteralValue
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
    elif spec.value_type is PortType.FLOAT:
        editor = FloatParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.INT:
        editor = IntParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.BOOL:
        editor = BoolParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.STRING:
        editor = StringParameterEditor(parameter, on_changed)
    elif spec.value_type is PortType.COLOR:
        editor = ColorParameterEditor(parameter, on_changed)
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
        self.editingFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
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
        self.editingFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
        self._on_changed(int(self.value()))


class BoolParameterEditor(QCheckBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        if isinstance(parameter.value, bool):
            self.setChecked(parameter.value)
        self.toggled.connect(self._commit)

    @Slot(bool)
    def _commit(self, checked: bool) -> None:
        self._on_changed(checked)


class StringParameterEditor(QLineEdit):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        if isinstance(parameter.value, str):
            self.setText(parameter.value)
        self.editingFinished.connect(self._commit)

    @Slot()
    def _commit(self) -> None:
        self._on_changed(self.text())


class ChoiceParameterEditor(QComboBox):
    def __init__(self, parameter: ParameterViewModel, on_changed: ParameterChanged) -> None:
        super().__init__()
        self._on_changed = on_changed
        for value in parameter.spec.choices:
            self.addItem(str(value), value)
        index = self.findData(parameter.value)
        if index >= 0:
            self.setCurrentIndex(index)
        self.currentIndexChanged.connect(self._commit)

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
        self._on_changed(self._value)

    def _update_swatch(self) -> None:
        color = QColor.fromRgbF(self._value.r, self._value.g, self._value.b, self._value.a)
        self.setStyleSheet(f"background-color: {color.name(QColor.NameFormat.HexArgb)};")
