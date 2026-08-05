"""Reusable inspector controls for the common visual-to-musical parameter group."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QWidget

from synesthesia_machine.graph import LiteralValue
from synesthesia_machine.midi import CUSTOM_SCALE_ID
from synesthesia_machine.ui.parameter_editors import create_parameter_editor
from synesthesia_machine.ui.view_models import ParameterGroupViewModel

type GroupParameterChanged = Callable[[str, LiteralValue], None]


class MusicalParameterEditor(QGroupBox):
    """One shared UI-only editor for the stable common musical parameter IDs."""

    def __init__(
        self,
        group: ParameterGroupViewModel,
        on_changed: GroupParameterChanged,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(group.spec.label, parent)
        self.setObjectName(f"parameter_group_{group.spec.id}")
        self.editors: dict[str, QWidget] = {}
        layout = QFormLayout(self)
        values = {parameter.spec.id: parameter.value for parameter in group.parameters}
        for parameter in group.parameters:
            parameter_id = parameter.spec.id
            editor = create_parameter_editor(
                parameter,
                lambda value, parameter_id=parameter_id: on_changed(parameter_id, value),
            )
            self.editors[parameter_id] = editor
            layout.addRow(parameter.spec.label, editor)
        scale_editor = self.editors.get("scale")
        if isinstance(scale_editor, QComboBox):
            scale_editor.currentIndexChanged.connect(self._scale_changed)
        self.set_custom_scale_enabled(values.get("scale") == CUSTOM_SCALE_ID)

    @Slot(int)
    def _scale_changed(self, index: int) -> None:
        del index
        scale_editor = self.editors.get("scale")
        if isinstance(scale_editor, QComboBox):
            self.set_custom_scale_enabled(scale_editor.currentData() == CUSTOM_SCALE_ID)

    def set_custom_scale_enabled(self, enabled: bool) -> None:
        editor = self.editors.get("custom_scale_mask")
        if editor is not None:
            editor.setEnabled(enabled)
