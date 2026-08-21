"""Offscreen editor shell, scene projection, scalar editors, and lifecycle tests."""

from collections.abc import Iterator, Mapping
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyleOptionGraphicsItem,
    QWidget,
)

import synesthesia_machine.ui.parameter_editors as parameter_editors_module
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import (
    ColorValue,
    DeviceKind,
    FrameContext,
    NumericMatrix,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.graph import GraphCompiler, GraphDocument
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
from synesthesia_machine.ui.graphics import (
    ConnectionGraphicsItem,
    NodeGraphicsItem,
    TemporaryConnectionGraphicsItem,
)
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
from synesthesia_machine.ui.view_models import ConnectionViewModel, ParameterViewModel
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
    assert menus == ("File", "Edit", "View", "Graph", "Outputs", "Help")
    assert window.centralWidget() is window.view
    assert window.library_dock.widget() is window.library
    assert window.inspector_dock.widget() is window.inspector
    assert window.profiler_dock.isHidden()
    assert window.profiler_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable
    assert (
        window.action_registry.require("randomize_parameters").text().replace("&", "")
        == "Randomize Parameters"
    )
    assert (
        window.action_registry.require("randomize_nodes").text().replace("&", "")
        == "Randomize Nodes"
    )
    assert (
        window.action_registry.require("organize_graph").text().replace("&", "") == "Organize Graph"
    )
    organize_button = window.transport_toolbar.widgetForAction(
        window.action_registry.require("organize_graph")
    )
    assert organize_button is not None
    assert organize_button.objectName() == "organize_graph_button"
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


def test_device_parameter_editor_shows_labels_but_commits_stable_id(qapp: QApplication) -> None:
    del qapp
    edits: list[object] = []
    spec = ParameterSpec(
        "output_port",
        "MIDI output port",
        PortType.STRING,
        "raw-port-2",
        device_kind=DeviceKind.MIDI_OUTPUT,
    )
    editor = create_parameter_editor(
        ParameterViewModel(spec, spec.default, False),
        edits.append,
        dynamic_choices=(("Friendly one", "raw-port-1"), ("Friendly two", "raw-port-2")),
    )

    assert isinstance(editor, QComboBox)
    assert editor.currentText() == "Friendly two"
    editor.setCurrentIndex(0)
    assert edits == ["raw-port-1"]


def test_audio_device_parameter_has_a_clear_default_before_discovery(window: MainWindow) -> None:
    spec = ParameterSpec(
        "output_device",
        "Output audio device",
        PortType.STRING,
        "",
        device_kind=DeviceKind.AUDIO_OUTPUT,
    )
    choices = window.session.device_parameter_choices(ParameterViewModel(spec, "", False))

    assert choices == (("System default audio output", ""),)


def test_horizontal_wheel_input_does_not_zoom_canvas(window: MainWindow) -> None:
    initial_scale = window.view.transform().m11()
    event = QWheelEvent(
        QPointF(10.0, 10.0),
        QPointF(10.0, 10.0),
        QPoint(),
        QPoint(120, 0),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )

    window.view.wheelEvent(event)

    assert window.view.transform().m11() == initial_scale


def test_selection_uses_one_scene_level_outline_and_tracks_node_bounds(
    window: MainWindow,
) -> None:
    first = window.session.add_node("synmachine.utility.number", (20.0, 30.0))
    second = window.session.add_node("synmachine.utility.number", (360.0, 140.0))
    window.scene.select_node_ids({first, second})

    first_bounds = window.scene.node_items[first].body_scene_rect
    second_bounds = window.scene.node_items[second].body_scene_rect
    margin = DEFAULT_THEME.metrics.selection_outline_margin
    assert margin == 2.0
    assert (
        DEFAULT_THEME.color("selection_outline").lightnessF()
        < DEFAULT_THEME.color("selection").lightnessF()
    )
    expected = first_bounds.united(second_bounds).adjusted(-margin, -margin, margin, margin)
    assert window.scene.selection_outline_rect == expected

    window.scene.node_items[second].setPos(520.0, 220.0)
    assert window.scene.selection_outline_rect.contains(
        window.scene.node_items[second].body_scene_rect
    )
    window.scene.clearSelection()
    assert window.scene.selection_outline_rect.isEmpty()


def test_randomize_parameters_action_changes_only_selected_nodes_and_is_one_undo_step(
    window: MainWindow,
) -> None:
    first = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    second = window.session.add_node("synmachine.utility.number", (300.0, 0.0))
    before_first = window.session.document.node(first)
    before_second = window.session.document.node(second)
    command_count = window.session.undo_stack.count()
    window.scene.select_node_ids({first})

    window.randomize_parameters()

    assert window.session.document.node(first) != before_first
    assert window.session.document.node(second) == before_second
    assert window.scene.selected_node_ids() == {first}
    assert window.session.undo_stack.count() == command_count + 1
    window.session.undo_stack.undo()
    assert window.session.document.node(first) == before_first


def test_randomize_nodes_action_replaces_and_selects_new_nodes(window: MainWindow) -> None:
    source = window.session.add_node("synmachine.utility.number", (0.0, 0.0))
    selected = window.session.add_node("synmachine.utility.pass_through", (240.0, 0.0))
    window.session.add_connection(source, "value", selected, "value")
    command_count = window.session.undo_stack.count()
    window.scene.select_node_ids({selected})

    window.randomize_nodes()

    replacement_ids = window.scene.selected_node_ids()
    assert replacement_ids
    assert selected not in replacement_ids
    assert window.session.document.node(selected) is None
    assert window.session.document.node(source) is not None
    assert window.session.undo_stack.count() == command_count + 1
    window.session.undo_stack.undo()
    assert window.session.document.node(selected) is not None


def test_randomize_nodes_without_selection_generates_complete_graph(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    registry = create_application_registry()
    generated_window = MainWindow(
        registry,
        paths_for(tmp_path),
        InProcessEngineClient(registry),
        settings=settings_for(tmp_path),
        offer_recovery=False,
    )
    try:
        generated_window.randomize_nodes()
        qapp.processEvents()
        snapshot = generated_window.session.document.snapshot()
        definitions = tuple(registry.require(node.type_id) for node in snapshot.nodes)

        assert GraphCompiler(registry).compile(snapshot).report.is_valid
        assert any(definition.execution_kind is ExecutionKind.SOURCE for definition in definitions)
        assert any(definition.execution_kind is ExecutionKind.SINK for definition in definitions)
        assert snapshot.connections
        assert not generated_window.scene.selected_node_ids()
    finally:
        generated_window.session.new_document()
        generated_window.close()


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


def test_library_double_click_signal_pair_adds_exactly_one_node(window: MainWindow) -> None:
    matches = window.library.tree.findItems(
        "Number",
        Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive,
        0,
    )
    assert len(matches) == 1
    item = matches[0]
    count_before = len(window.session.document.nodes)

    # QTreeWidget emits both signals for a mouse double-click on Windows.
    window.library.tree.itemDoubleClicked.emit(item, 0)
    window.library.tree.itemActivated.emit(item, 0)

    assert len(window.session.document.nodes) == count_before + 1
    assert len(window.scene.node_items) == count_before + 1


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


def _connection_view_model(type_name: str) -> ConnectionViewModel:
    return ConnectionViewModel(
        connection_id=UUID("00000000-0000-0000-0000-0000000000e1"),
        source_node_id=UUID("00000000-0000-0000-0000-0000000000e2"),
        source_port_id="out",
        destination_node_id=UUID("00000000-0000-0000-0000-0000000000e3"),
        destination_port_id="in",
        type_name=type_name,
        issues=(),
    )


@pytest.mark.parametrize(
    ("type_name", "value_pill", "image_pill"),
    [
        ("INT", True, False),
        ("FLOAT", True, False),
        ("IMAGE", False, True),
        ("CHANNEL", False, True),
        ("BOOLEAN", False, False),
        ("", False, False),
    ],
)
def test_connection_pill_predicates_follow_link_type_family(
    type_name: str, value_pill: bool, image_pill: bool
) -> None:
    item = ConnectionGraphicsItem(_connection_view_model(type_name), DEFAULT_THEME)
    assert item.takes_value_pill() is value_pill
    assert item.takes_image_pill() is image_pill


def test_connection_pill_state_toggles_and_stores_previews() -> None:
    item = ConnectionGraphicsItem(_connection_view_model("FLOAT"), DEFAULT_THEME)
    assert item._preview_visible is True
    assert item._value_text is None
    assert item._image is None

    item.set_value_preview("42")
    assert item._value_text == "42"
    item.set_value_preview("42")  # no-op when unchanged
    assert item._value_text == "42"

    item.set_preview_visible(False)
    assert item._preview_visible is False
    item.set_preview_visible(True)
    assert item._preview_visible is True

    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    item.set_image_preview(image)
    assert item._image is image
    item.set_image_preview(None)
    assert item._image is None


def test_scalar_editor_factory_supports_all_literal_types() -> None:
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


def test_odd_integer_editor_snaps_even_gaussian_kernel_values(qapp: QApplication) -> None:
    spec = (
        create_application_registry()
        .require("synmachine.image.gaussian_blur")
        .parameter("kernel_width")
    )
    assert spec is not None
    assert spec.step == 2
    edits: list[object] = []
    editor = create_parameter_editor(ParameterViewModel(spec, spec.default, False), edits.append)
    assert isinstance(editor, QSpinBox)

    editor.setValue(4)
    qapp.processEvents()

    assert editor.value() == 5
    assert edits[-1] == 5


def test_float_editor_preserves_and_clamps_tiny_positive_bounds(qapp: QApplication) -> None:
    spec = ParameterSpec("epsilon", "Epsilon", PortType.FLOAT, 1e-6, minimum=1e-12)
    edits: list[object] = []
    editor = create_parameter_editor(ParameterViewModel(spec, spec.default, False), edits.append)
    assert isinstance(editor, QDoubleSpinBox)

    editor.setValue(0.0)
    qapp.processEvents()

    assert editor.decimals() == 12
    assert editor.value() == pytest.approx(1e-12)
    assert edits[-1] == pytest.approx(1e-12)


def test_color_dialog_uses_real_window_parent_instead_of_graph_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = QWidget()
    captured: dict[str, object] = {}

    def fake_get_color(
        initial: QColor,
        parent: QWidget | None,
        title: str,
        options: QColorDialog.ColorDialogOption,
    ) -> QColor:
        captured.update(initial=initial, parent=parent, title=title, options=options)
        return QColor()

    monkeypatch.setattr(parameter_editors_module, "_color_dialog_parent", lambda _editor: owner)
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(fake_get_color))
    spec = ParameterSpec(
        "border_colour",
        "Border colour",
        PortType.COLOR,
        ColorValue(0.0, 0.0, 0.0, 0.0),
    )
    editor = create_parameter_editor(
        ParameterViewModel(spec, spec.default, False), lambda _value: None
    )

    editor.click()

    assert captured["parent"] is owner
    assert captured["options"] == QColorDialog.ColorDialogOption.ShowAlphaChannel


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

    float_editor.setValue(0.25)
    qapp.processEvents()
    edit_count = len(edits)
    value_line_editor = float_editor.value_editor.lineEdit()
    start = QPoint(20, value_line_editor.height() // 2)
    finish = QPoint(60, value_line_editor.height() // 2)
    QTest.mousePress(value_line_editor, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(value_line_editor, finish)
    qapp.processEvents()
    assert float_editor.value() > 0.25
    assert len(edits) == edit_count
    QTest.mouseRelease(value_line_editor, Qt.MouseButton.LeftButton, pos=finish)
    qapp.processEvents()
    assert len(edits) == edit_count + 1
    assert edits[-1] == pytest.approx(float_editor.value())

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

    technical_spec = ParameterSpec("int_steps", "Steps", PortType.INT, 30, minimum=5, maximum=30)
    technical_editor = create_parameter_editor(
        ParameterViewModel(technical_spec, 30, False), edits.append
    )
    assert isinstance(technical_editor, QSpinBox)


def test_numeric_value_fields_scrub_horizontally_and_commit_on_release(
    qapp: QApplication,
) -> None:
    edits: list[object] = []
    editor = create_parameter_editor(
        ParameterViewModel(
            ParameterSpec("gain", "Gain", PortType.FLOAT, 1.0),
            1.0,
            False,
        ),
        edits.append,
    )
    assert isinstance(editor, QDoubleSpinBox)
    editor.resize(160, 26)
    editor.show()
    qapp.processEvents()
    line_editor = editor.lineEdit()
    start = QPoint(30, line_editor.height() // 2)
    finish = QPoint(70, line_editor.height() // 2)

    QTest.mousePress(line_editor, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(line_editor, finish)
    qapp.processEvents()

    assert editor.value() > 1.0
    assert not edits

    QTest.mouseRelease(line_editor, Qt.MouseButton.LeftButton, pos=finish)
    qapp.processEvents()

    assert edits[-1] == pytest.approx(editor.value())
    editor.close()


def test_builtin_slider_metadata_reserves_sliders_for_continuous_spectra() -> None:
    registry = create_application_registry()
    technical_parameters = (
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

    hue = registry.require("synmachine.image.hue").parameter("turns")
    assert hue is not None
    hue_editor = create_parameter_editor(
        ParameterViewModel(hue, hue.default, False), lambda _value: None
    )
    assert isinstance(hue_editor, FloatRangeParameterEditor)
    assert (hue_editor.minimum(), hue_editor.maximum()) == (0.0, 1.0)


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

    utility = roots[-1]
    utility_children = [utility.child(index) for index in range(utility.childCount())]
    subgroup_start = next(
        index for index, child in enumerate(utility_children) if child.childCount() > 0
    )
    assert subgroup_start > 0
    assert all(child.childCount() == 0 for child in utility_children[:subgroup_start])
    assert all(child.childCount() > 0 for child in utility_children[subgroup_start:])

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


def test_error_badge_paints_red_dot_with_visible_exclamation(window: MainWindow) -> None:
    node_id = window.session.add_node("synmachine.utility.math", (80.0, 120.0))
    item = window.scene.node_items[node_id]
    assert item.view_model.issues
    image = QImage(
        round(item.node_width),
        round(DEFAULT_THEME.metrics.header_height),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    item.paint(painter, QStyleOptionGraphicsItem())
    painter.end()

    badge = item._issue_badge_rect()
    pixels = [
        image.pixelColor(x, y)
        for y in range(round(badge.top()), round(badge.bottom()) + 1)
        for x in range(round(badge.left()), round(badge.right()) + 1)
    ]
    assert any(color.red() > 200 and color.green() < 150 for color in pixels)
    assert any(color.green() > 170 and color.blue() > 170 for color in pixels)


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
