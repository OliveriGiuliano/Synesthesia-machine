"""Offscreen editor shell, scene projection, scalar editors, and lifecycle tests."""

from collections.abc import Iterator, Mapping
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyleOptionGraphicsItem,
)

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    ColorValue,
    FrameContext,
    NumericMatrix,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes import (
    ExecutionKind,
    NodeDefinition,
    NodeRegistry,
    ParameterEditorHint,
    ParameterSpec,
    ResetReason,
)
from synesthesia_machine.nodes.utility import create_utility_registry
from synesthesia_machine.persistence import save_graph
from synesthesia_machine.runtime import InProcessEngineClient
from synesthesia_machine.ui.graphics import NodeGraphicsItem, TemporaryConnectionGraphicsItem
from synesthesia_machine.ui.main_window import MainWindow
from synesthesia_machine.ui.parameter_editors import (
    DirectDragSlider,
    FilePathParameterEditor,
    FloatRangeParameterEditor,
    IntRangeParameterEditor,
    create_parameter_editor,
)
from synesthesia_machine.ui.theme import DEFAULT_THEME, node_category_color
from synesthesia_machine.ui.tooltips import TOOLTIP_LINE_WIDTH, format_tooltip
from synesthesia_machine.ui.view_models import ParameterViewModel
from synesthesia_machine.ui.widgets import NodeLibrary, NodeSearchDialog, SearchCandidate


class _NoopRuntime:
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del inputs, parameters, context
        return {}

    def reset(self, reason: ResetReason) -> None:
        del reason

    def close(self) -> None:
        return


def paths_for(root: Path) -> ApplicationPaths:
    data = root / "data"
    return ApplicationPaths(data, data / "logs", data / "recovery")


def settings_for(root: Path) -> QSettings:
    return QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(qapp: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    del qapp
    registry = create_utility_registry()
    result = MainWindow(
        registry,
        paths_for(tmp_path),
        InProcessEngineClient(registry),
        settings=settings_for(tmp_path),
        offer_recovery=False,
    )
    yield result
    result.session.new_document()
    result.close()


def test_shell_has_fixed_structure_actions_and_accessible_controls(window: MainWindow) -> None:
    menus = tuple(action.text().replace("&", "") for action in window.menuBar().actions())
    assert menus == ("File", "Edit", "View", "Graph", "MIDI", "Help")
    assert window.centralWidget() is window.view
    assert window.library_dock.widget() is window.library
    assert window.inspector_dock.widget() is window.inspector
    assert window.accessibleName() == "Synesthesia Machine graph editor"
    assert window.library.search.accessibleName()
    assert window.library.tree.accessibleName()
    assert window.inspector.validation.accessibleName()
    assert window.devicePixelRatioF() > 0.0
    style_sheet = DEFAULT_THEME.style_sheet()
    assert "QDockWidget::title" in style_sheet
    assert "QTabBar::tab:selected" in style_sheet

    for action in window.action_registry.values():
        assert action.objectName().startswith("action_")
        assert action.statusTip()
    for key in ("new", "open", "save", "undo", "redo", "copy", "paste", "duplicate"):
        assert not window.action_registry.require(key).shortcut().isEmpty()


def test_palette_and_graph_search_index_registry_aliases(window: MainWindow) -> None:
    window.library.search.setText("constant")
    matches = window.library.tree.findItems(
        "Number",
        Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive,
        0,
    )
    assert len(matches) == 1

    dialog = NodeSearchDialog(
        (SearchCandidate(definition) for definition in window.registry.definitions()),
        window,
    )
    dialog.search.setText("calculator")
    assert dialog.results.count() == 1
    selected = dialog.selected_candidate()
    assert selected is not None
    assert selected.definition.type_id == "synmachine.utility.math"


def test_scene_parameter_edit_duplicate_copy_paste_and_undo(
    window: MainWindow, qapp: QApplication
) -> None:
    node_id = window.session.add_node("synmachine.utility.number", (80.0, 120.0))
    item = window.scene.node_items[node_id]
    assert isinstance(item, NodeGraphicsItem)
    assert (item.pos().x(), item.pos().y()) == (80.0, 120.0)

    proxy = item.parameter_editors["float_value"]
    editor = proxy.widget()
    assert isinstance(editor, QDoubleSpinBox)
    editor.setValue(6.5)
    editor.editingFinished.emit()
    qapp.processEvents()
    assert window.session.document.node(node_id).parameters["float_value"] == 6.5  # type: ignore[union-attr]

    window.scene.select_node_ids({node_id})
    window.duplicate_selection()
    duplicate_ids = set(window.scene.node_items) - {node_id}
    assert len(duplicate_ids) == 1
    assert next(iter(duplicate_ids)) != node_id
    window.session.undo_stack.undo()
    assert set(window.scene.node_items) == {node_id}

    window.scene.select_node_ids({node_id})
    window.copy_selection()
    assert window.action_registry.require("paste").isEnabled()
    window.paste_selection()
    assert len(window.scene.node_items) == 2
    assert node_id in window.scene.node_items


def test_scene_move_is_view_only_until_one_multi_node_command_commits(
    window: MainWindow,
) -> None:
    first = window.session.add_node("synmachine.utility.number", (10.0, 20.0))
    second = window.session.add_node("synmachine.utility.number", (40.0, 50.0))
    window.scene.select_node_ids({first, second})
    origins = window.scene.selected_node_positions()
    count_before = window.session.undo_stack.count()

    window.scene.node_items[first].setPos(110.0, 120.0)
    window.scene.node_items[second].setPos(140.0, 150.0)

    assert window.session.document.node(first).position == (10.0, 20.0)  # type: ignore[union-attr]
    assert window.session.document.node(second).position == (40.0, 50.0)  # type: ignore[union-attr]

    window.scene.commit_node_move(origins)

    assert window.session.undo_stack.count() == count_before + 1
    assert window.session.document.node(first).position == (110.0, 120.0)  # type: ignore[union-attr]
    assert window.session.document.node(second).position == (140.0, 150.0)  # type: ignore[union-attr]
    window.session.undo_stack.undo()
    assert window.session.document.node(first).position == (10.0, 20.0)  # type: ignore[union-attr]
    assert window.session.document.node(second).position == (40.0, 50.0)  # type: ignore[union-attr]


def test_reverse_cable_gesture_highlights_connects_and_has_selectable_hit_shape(
    window: MainWindow,
) -> None:
    source_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    target_id = window.session.add_node("synmachine.utility.math", (320.0, 0.0))
    source = window.scene.port_item(source_id, "value", True)
    destination = window.scene.port_item(target_id, "a", False)
    assert source is not None and destination is not None

    window.scene.begin_connection_drag(destination, destination.scenePos())
    assert source.compatible is True
    window.scene.end_connection_drag(source.scenePos())

    connection = window.session.document.connections[0]
    assert connection.source_node_id == source_id
    assert connection.source_port_id == "value"
    assert connection.destination_node_id == target_id
    assert connection.destination_port_id == "a"
    assert source.compatible is None
    assert destination.compatible is None

    cable = window.scene.connection_items[connection.id]
    midpoint = cable.path.pointAtPercent(0.5)
    assert cable.shape().contains(midpoint)
    assert cable.shape().boundingRect().height() > cable.path.boundingRect().height()


def test_cable_drop_on_empty_emits_origin_and_position(window: MainWindow) -> None:
    source_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    source = window.scene.port_item(source_id, "value", True)
    assert source is not None
    dropped: list[tuple[object, object]] = []

    def capture_drop(port: object, position: object) -> None:
        dropped.append((port, position))

    window.scene.connectionDroppedOnEmpty.disconnect()
    window.scene.connectionDroppedOnEmpty.connect(capture_drop)
    empty_position = QPointF(1700.0, 1300.0)

    window.scene.begin_connection_drag(source, source.scenePos())
    window.scene.end_connection_drag(empty_position)

    assert dropped == [(source.view_model, empty_position)]
    assert window.session.document.connections == ()


def test_right_click_cancels_active_connection_drag_without_leaving_a_frozen_cable(
    window: MainWindow,
    qapp: QApplication,
) -> None:
    source_id = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    target_id = window.session.add_node("synmachine.utility.math", (320.0, 0.0))
    source = window.scene.port_item(source_id, "value", True)
    target = window.scene.port_item(target_id, "a", False)
    assert source is not None and target is not None
    window.resize(900, 600)
    window.show()
    window.view.centerOn(source)
    qapp.processEvents()

    window.scene.begin_connection_drag(source, source.scenePos() + QPointF(120.0, 40.0))
    assert window.scene.connection_drag_active
    assert target.compatible is True

    QTest.mouseClick(
        window.view.viewport(),
        Qt.MouseButton.RightButton,
        pos=window.view.mapFromScene(source.scenePos() + QPointF(120.0, 40.0)),
    )
    qapp.processEvents()

    assert not window.scene.connection_drag_active
    assert window.scene._temporary is None
    assert source.compatible is None
    assert target.compatible is None


def test_temporary_connection_explicitly_disables_path_fill() -> None:
    item = TemporaryConnectionGraphicsItem(DEFAULT_THEME)
    item.set_endpoints(QPointF(10.0, 10.0), QPointF(190.0, 90.0))
    image = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setBrush(QBrush(Qt.GlobalColor.red))

    item.paint(painter, QStyleOptionGraphicsItem())

    assert painter.brush().style() is Qt.BrushStyle.NoBrush
    painter.end()


def test_scalar_editor_factory_supports_all_phase_2_literal_types() -> None:
    cases = (
        (ParameterSpec("float_value", "Float", PortType.FLOAT, 1.0), 1.0, QDoubleSpinBox),
        (ParameterSpec("int_value", "Int", PortType.INT, 1), 1, QSpinBox),
        (ParameterSpec("bool_value", "Bool", PortType.BOOL, True), True, QCheckBox),
        (ParameterSpec("text_value", "Text", PortType.STRING, "x"), "x", QLineEdit),
        (
            ParameterSpec("choice_value", "Choice", PortType.STRING, "A", choices=("A", "B")),
            "A",
            QComboBox,
        ),
        (
            ParameterSpec("color_value", "Color", PortType.COLOR, ColorValue(0.1, 0.2, 0.3)),
            ColorValue(0.1, 0.2, 0.3),
            QPushButton,
        ),
    )
    for spec, value, expected_type in cases:
        editor = create_parameter_editor(
            ParameterViewModel(spec, value, False), lambda _value: None
        )
        assert isinstance(editor, expected_type)
        assert editor.accessibleName() == spec.label


def test_slider_hint_is_explicit_and_sliders_display_and_drag_their_value(
    qapp: QApplication,
) -> None:
    edits: list[object] = []
    float_spec = ParameterSpec(
        "gain",
        "Gain",
        PortType.FLOAT,
        0.25,
        minimum=0.0,
        maximum=1.0,
        editor_hint=ParameterEditorHint.SLIDER,
    )
    float_editor = create_parameter_editor(
        ParameterViewModel(float_spec, 0.25, False), edits.append
    )
    assert isinstance(float_editor, FloatRangeParameterEditor)
    assert isinstance(float_editor.slider, DirectDragSlider)
    assert (float_editor.minimum(), float_editor.maximum()) == (0.0, 1.0)
    assert isinstance(float_editor.value_editor, QDoubleSpinBox)
    assert float_editor.value_editor.value() == pytest.approx(0.25)
    assert float_editor.value_editor.property("parameterValueInput") is True
    assert 'QDoubleSpinBox[parameterValueInput="true"]' in DEFAULT_THEME.style_sheet()
    float_editor.setValue(0.75)
    qapp.processEvents()
    assert edits[-1] == pytest.approx(0.75)
    assert float_editor.value_editor.value() == pytest.approx(0.75)

    float_editor.value_editor.setFocus()
    float_editor.value_editor.selectAll()
    QTest.keyClicks(float_editor.value_editor, "0.625")
    QTest.keyClick(float_editor.value_editor, Qt.Key.Key_Return)
    qapp.processEvents()
    assert edits[-1] == pytest.approx(0.625)
    assert float_editor.value() == pytest.approx(0.625)

    float_editor.resize(220, 24)
    float_editor.show()
    qapp.processEvents()
    QTest.mousePress(
        float_editor.slider,
        Qt.MouseButton.LeftButton,
        pos=QPoint(12, float_editor.slider.height() // 2),
    )
    QTest.mouseMove(
        float_editor.slider,
        QPoint(float_editor.slider.width() - 12, float_editor.slider.height() // 2),
    )
    QTest.mouseRelease(
        float_editor.slider,
        Qt.MouseButton.LeftButton,
        pos=QPoint(float_editor.slider.width() - 12, float_editor.slider.height() // 2),
    )
    qapp.processEvents()
    assert float_editor.value() > 0.9

    int_spec = ParameterSpec(
        "voices",
        "Voices",
        PortType.INT,
        4,
        minimum=1,
        maximum=16,
        editor_hint=ParameterEditorHint.SLIDER,
    )
    int_editor = create_parameter_editor(ParameterViewModel(int_spec, 4, False), edits.append)
    assert isinstance(int_editor, IntRangeParameterEditor)
    assert (int_editor.minimum(), int_editor.maximum()) == (1, 16)
    int_editor.setValue(12)
    qapp.processEvents()
    assert edits[-1] == 12
    int_editor.value_editor.setFocus()
    int_editor.value_editor.selectAll()
    QTest.keyClicks(int_editor.value_editor, "7")
    QTest.keyClick(int_editor.value_editor, Qt.Key.Key_Return)
    qapp.processEvents()
    assert edits[-1] == 7
    assert int_editor.slider.value() == 7

    technical_spec = ParameterSpec(
        "preview_fps", "Preview FPS", PortType.INT, 30, minimum=5, maximum=30
    )
    technical_editor = create_parameter_editor(
        ParameterViewModel(technical_spec, 30, False), edits.append
    )
    assert isinstance(technical_editor, QSpinBox)


def test_builtin_slider_metadata_reserves_sliders_for_continuous_spectra() -> None:
    registry = create_application_registry()
    technical_parameters = (
        ("synmachine.visualization.channel_display", "preview_fps"),
        ("synmachine.visualization.channel_display", "max_dimension"),
        ("synmachine.synesthesia.channel_to_pitch", "midi_channel"),
        ("synmachine.synesthesia.channel_to_pitch", "maximum_polyphony"),
    )
    for type_id, parameter_id in technical_parameters:
        spec = registry.require(type_id).parameter(parameter_id)
        assert spec is not None
        editor = create_parameter_editor(
            ParameterViewModel(spec, spec.default, False), lambda _value: None
        )
        assert isinstance(editor, QSpinBox)

    volume = registry.require("synmachine.output.generate_audio").parameter("volume")
    assert volume is not None
    volume_editor = create_parameter_editor(
        ParameterViewModel(volume, volume.default, False), lambda _value: None
    )
    assert isinstance(volume_editor, FloatRangeParameterEditor)


def test_node_category_palette_is_unique_and_applied_to_library_names(
    qapp: QApplication,
) -> None:
    registry = create_application_registry()
    categories = {definition.category for definition in registry.definitions()}
    colors = {node_category_color(category).name() for category in categories}
    assert len(colors) == len(categories)

    library = NodeLibrary(registry)
    library.show()
    qapp.processEvents()
    roots = [library.tree.topLevelItem(index) for index in range(library.tree.topLevelItemCount())]
    assert [root.text(0) for root in roots] == [
        "Inputs",
        "Image",
        "Synesthesia",
        "Outputs",
        "Visualization",
        "Utility",
    ]
    assert roots[0].foreground(0).color() == node_category_color("Input")

    image = roots[1]
    assert [image.child(index).text(0) for index in range(image.childCount())] == [
        "Adjustments",
        "Analysis",
        "Channels",
        "Compositing",
        "Dimensions",
        "Filters",
        "Utilities",
    ]
    adjustment = image.child(0)
    assert adjustment.foreground(0).color() == node_category_color("Image / Adjustment")
    assert adjustment.background(0).color().alpha() > 0
    adjustment_names = [adjustment.child(index).text(0) for index in range(adjustment.childCount())]
    assert "Image Add Scalar" in adjustment_names

    image_hues = {
        node_category_color(category).hsvHue()
        for category in categories
        if category.startswith("Image / ")
    }
    assert max(image_hues) - min(image_hues) < 40

    dialog = NodeSearchDialog(SearchCandidate(definition) for definition in registry.definitions())
    dialog.search.setText("region grid notes")
    assert dialog.results.count() == 1
    result = dialog.results.item(0)
    candidate = dialog.selected_candidate()
    assert candidate is not None
    assert result.foreground().color() == node_category_color(candidate.definition.category)
    dialog.close()
    library.close()


def test_pressing_enter_in_embedded_numeric_editor_commits_without_destroying_signal_sender(
    window: MainWindow,
    qapp: QApplication,
) -> None:
    node_id = window.session.add_node("synmachine.utility.number", (80.0, 120.0))
    editor = window.scene.node_items[node_id].parameter_editors["float_value"].widget()
    assert isinstance(editor, QDoubleSpinBox)
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, "12.5")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    qapp.processEvents()

    assert window.session.document.node(node_id).parameters["float_value"] == 12.5  # type: ignore[union-attr]
    replacement = window.scene.node_items[node_id].parameter_editors["float_value"].widget()
    assert replacement is not None and replacement is not editor


def test_embedded_spinbox_arrow_buttons_commit_both_directions(
    window: MainWindow,
    qapp: QApplication,
) -> None:
    node_id = window.session.add_node("synmachine.utility.number", (80.0, 120.0))
    editor = window.scene.node_items[node_id].parameter_editors["int_value"].widget()
    assert isinstance(editor, QSpinBox)
    original = editor.value()

    QTest.mouseClick(
        editor,
        Qt.MouseButton.LeftButton,
        pos=QPoint(editor.width() - 8, 5),
    )
    qapp.processEvents()
    assert window.session.document.node(node_id).parameters["int_value"] == original + 1  # type: ignore[union-attr]

    replacement = window.scene.node_items[node_id].parameter_editors["int_value"].widget()
    assert isinstance(replacement, QSpinBox)
    QTest.mouseClick(
        replacement,
        Qt.MouseButton.LeftButton,
        pos=QPoint(replacement.width() - 8, replacement.height() - 5),
    )
    qapp.processEvents()
    assert window.session.document.node(node_id).parameters["int_value"] == original  # type: ignore[union-attr]


def test_node_parameter_rows_have_contextual_hover_help(window: MainWindow) -> None:
    node_id = window.session.add_node("synmachine.utility.number", (80.0, 120.0))
    item = window.scene.node_items[node_id]

    node_help = item._tooltip_for_position(QPointF(20.0, 10.0))
    parameter_help = item._tooltip_for_position(QPointF(20.0, item._parameter_rows["float_value"]))

    assert node_help == item.view_model.description
    assert "float value" in parameter_help.lower()
    assert parameter_help != node_help


def test_long_hover_help_uses_bounded_multiline_rich_text(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    registry = create_application_registry()
    full_window = MainWindow(
        registry,
        paths_for(tmp_path),
        InProcessEngineClient(registry),
        settings=settings_for(tmp_path),
        offer_recovery=False,
    )
    try:
        node_id = full_window.session.add_node("synmachine.synesthesia.region_grid", (80.0, 120.0))
        item = full_window.scene.node_items[node_id]

        node_help = item._tooltip_for_position(QPointF(20.0, 10.0))
        metric_help = item._tooltip_for_position(QPointF(20.0, item._parameter_rows["metric"]))

        assert node_help.startswith("<qt>") and "<br>" in node_help
        assert metric_help.startswith("<qt>") and "<br>" in metric_help
    finally:
        full_window.session.new_document()
        full_window.close()


def test_tooltip_formatter_preserves_short_help_and_escapes_long_help() -> None:
    assert format_tooltip("Short contextual help.") == "Short contextual help."
    long_help = (
        "A <metric> explanation with safely escaped markup and bounded lines. " * 4
    ).strip()
    formatted = format_tooltip(long_help)

    assert formatted.startswith("<qt>")
    assert "&lt;metric&gt;" in formatted
    assert "<metric>" not in formatted
    assert "<br>" in formatted
    assert all(
        len(line.replace("&lt;", "<").replace("&gt;", ">")) <= TOOLTIP_LINE_WIDTH
        for line in formatted.removeprefix('<qt><div style="white-space: nowrap;">')
        .removesuffix("</div></qt>")
        .split("<br>")
    )


def test_video_file_path_editor_browses_and_commits_selected_path(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qapp
    selected = "C:/media/example.mkv"
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def choose_file(*args: object, **kwargs: object) -> tuple[str, str]:
        calls.append((args, kwargs))
        return selected, "Video files"

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        choose_file,
    )
    edits: list[object] = []
    spec = ParameterSpec("file_path", "File path", PortType.STRING, "")
    editor = create_parameter_editor(ParameterViewModel(spec, "", False), edits.append)

    assert isinstance(editor, FilePathParameterEditor)
    assert editor.browse_button.text() == "Browse…"
    editor.browse_button.click()
    assert editor.text() == selected
    assert edits == [selected]
    assert calls[0][1]["options"] == QFileDialog.Option.DontUseNativeDialog


def test_matrix_editor_commits_valid_nested_json_and_marks_invalid_input(
    qapp: QApplication,
) -> None:
    del qapp
    original = NumericMatrix(((1.0,),))
    changed: list[object] = []
    spec = ParameterSpec("kernel", "Kernel", PortType.MATRIX, original)
    editor = create_parameter_editor(
        ParameterViewModel(spec, original, False),
        changed.append,
    )
    assert isinstance(editor, QLineEdit)
    assert editor.text() == "[[1.0]]"

    editor.setText("[[0,1,0],[1,-4,1],[0,1,0]]")
    editor.editingFinished.emit()
    assert changed == [NumericMatrix(((0.0, 1.0, 0.0), (1.0, -4.0, 1.0), (0.0, 1.0, 0.0)))]
    assert editor.styleSheet() == ""

    editor.setText("[[1],[2,3]]")
    editor.editingFinished.emit()
    assert len(changed) == 1
    assert "#c44" in editor.styleSheet()

    editor.setText(f"[[{10**1000}]]")
    editor.editingFinished.emit()
    assert len(changed) == 1
    assert "#c44" in editor.styleSheet()


def test_connectable_parameter_keeps_disabled_literal_fallback_while_connected(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    parameter = ParameterSpec(
        "gain",
        "Gain",
        PortType.FLOAT,
        0.75,
        connectable=True,
        connected_port_type=PortType.FLOAT,
    )
    sink = NodeDefinition(
        "synmachine.test.connectable",
        1,
        "Connectable",
        "Test",
        "Connectable parameter test node.",
        (),
        (),
        (parameter,),
        ExecutionKind.SINK,
        _NoopRuntime,
    )
    registry = NodeRegistry((*create_utility_registry().definitions(), sink))
    editor_window = MainWindow(
        registry,
        paths_for(tmp_path),
        InProcessEngineClient(registry),
        settings=settings_for(tmp_path),
        offer_recovery=False,
    )
    source = editor_window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    target = editor_window.session.add_node(sink.type_id, (250.0, 0.0))
    editor_window.session.add_connection(source, "value", target, "gain")

    item = editor_window.scene.node_items[target]
    assert ("gain", False) in item.ports
    assert item.ports[("gain", False)].view_model.is_parameter
    literal_editor = item.parameter_editors["gain"].widget()
    assert literal_editor is not None
    assert not literal_editor.isEnabled()
    assert editor_window.session.document.node(target).parameters.get("gain", 0.75) == 0.75  # type: ignore[union-attr]

    editor_window.session.undo_stack.undo()
    restored_editor = editor_window.scene.node_items[target].parameter_editors["gain"].widget()
    assert restored_editor is not None and restored_editor.isEnabled()
    editor_window.session.new_document()
    editor_window.close()


def test_open_prompts_once_and_malformed_file_is_reported(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_document = GraphDocument()
    target_document.add_node("synmachine.utility.number", position=(10.0, 20.0))
    target_path = tmp_path / "target.synmachine.json"
    save_graph(target_path, target_document.snapshot())
    window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    prompts: list[str] = []

    def discard(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        del args, kwargs
        prompts.append("prompt")
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "warning", discard)
    assert window.open_path(target_path)
    assert prompts == ["prompt"]

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, detail: errors.append((title, detail)),
    )
    assert not window.open_path(malformed)
    assert errors and errors[0][0] == "Could not open graph"


def test_discard_button_from_real_unsaved_close_dialog_closes_window(window: MainWindow) -> None:
    window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    window.show()
    clicked: list[QMessageBox.StandardButton] = []

    def click_discard() -> None:
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QMessageBox):
            return
        button = dialog.button(QMessageBox.StandardButton.Discard)
        if button is not None:
            clicked.append(QMessageBox.StandardButton.Discard)
            button.click()

    QTimer.singleShot(0, click_discard)

    assert window.close()
    assert clicked == [QMessageBox.StandardButton.Discard]
    assert not window.isVisible()
    assert window._engine_closed


def test_recovery_offer_restores_dirty_session_and_explicit_save_discards_recovery(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = GraphDocument()
    node_id = document.add_node("synmachine.utility.number", position=(33.0, 44.0))
    recovery_path = window.autosave_store.save(document.snapshot())
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Open,
    )

    window._offer_recovery()
    assert window.session.document.node(node_id) is not None
    assert window.session.is_dirty

    explicit = tmp_path / "recovered.synmachine.json"
    assert window._save_to_path(explicit)
    assert explicit.exists()
    assert not recovery_path.exists()
    assert not window.session.is_dirty
