"""Inline help, navigable errors, and source-scoped transport tests."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QPointF
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QApplication, QLabel

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import ValidationIssue, ValidationReport, ValidationSeverity
from synesthesia_machine.ui.graphics import NodeGraphicsItem
from synesthesia_machine.ui.parameter_editors import FloatRangeParameterEditor
from synesthesia_machine.ui.session import DocumentSession
from synesthesia_machine.ui.theme import DEFAULT_THEME
from synesthesia_machine.ui.transport import resolve_transport_target
from synesthesia_machine.ui.widgets import InspectorPanel, ValidationIssuePanel

SOURCE_A = UUID("80000000-0000-0000-0000-000000000001")
SOURCE_B = UUID("80000000-0000-0000-0000-000000000002")
UTILITY = UUID("80000000-0000-0000-0000-000000000003")


def test_transport_resolver_targets_one_source_without_fanning_out() -> None:
    assert resolve_transport_target((), ()).target is None
    sole = resolve_transport_target((SOURCE_A,), (UTILITY,))
    assert sole.target == SOURCE_A and "sole source" in sole.message
    selected = resolve_transport_target((SOURCE_A, SOURCE_B), (SOURCE_B, UTILITY))
    assert selected.target == SOURCE_B and "selected source" in selected.message
    ambiguous = resolve_transport_target((SOURCE_A, SOURCE_B), (SOURCE_A, SOURCE_B))
    assert ambiguous.target is None and "exactly one" in ambiguous.message
    unselected = resolve_transport_target((SOURCE_A, SOURCE_B), (UTILITY,))
    assert unselected.target is None and "Select one source" in unselected.message


def test_validation_panel_exposes_actionable_detail_and_navigation_signal(
    qapp: QApplication,
) -> None:
    del qapp
    issue = ValidationIssue(
        ValidationSeverity.ERROR,
        "required_input_missing",
        "Input 'image' requires a connection",
        node_id=SOURCE_A,
        port_id="image",
    )
    panel = ValidationIssuePanel()
    activated: list[ValidationIssue] = []
    panel.issueActivated.connect(activated.append)
    panel.set_report(ValidationReport((issue,)))

    item = panel.issues.item(0)
    assert panel.summary.text() == "1 errors · 0 warnings"
    assert "Code: required_input_missing" in item.toolTip()
    assert "Port: image" in item.toolTip()
    panel._activate_issue(item)
    assert activated == [issue]


def test_node_title_and_error_badge_have_separate_tooltips(qapp: QApplication) -> None:
    del qapp
    registry = create_application_registry()
    session = DocumentSession(registry)
    node_id = session.add_node("synmachine.utility.math", (0.0, 0.0))
    node = next(node for node in session.view_model.nodes if node.node_id == node_id)
    item = NodeGraphicsItem(node, DEFAULT_THEME, session.set_parameter)

    assert node.issues
    title_help = item._tooltip_for_position(QPointF(20.0, 10.0))
    issue_help = item._tooltip_for_position(item._issue_badge_rect().center())

    assert node.description in title_help
    assert node.issues[0].message not in title_help
    assert node.issues[0].message in issue_help
    assert node.issues[0].code in issue_help
    assert node.description not in issue_help


def test_node_width_accounts_for_long_parameter_labels(qapp: QApplication) -> None:
    del qapp
    registry = create_application_registry()
    session = DocumentSession(registry)
    node_id = session.add_node("synmachine.input.load_video", (0.0, 0.0))
    node = next(node for node in session.view_model.nodes if node.node_id == node_id)
    item = NodeGraphicsItem(node, DEFAULT_THEME, session.set_parameter)
    label = next(
        parameter.spec.label
        for parameter in node.parameters
        if parameter.spec.id == "process_every_nth_frame"
    )
    label_width = QFontMetricsF(DEFAULT_THEME.body_font()).horizontalAdvance(label)
    editor_left = item.parameter_editors["process_every_nth_frame"].pos().x()

    assert item.node_width > DEFAULT_THEME.metrics.node_width
    assert editor_left - 10.0 - 13.0 >= label_width


def test_inspector_renders_parameter_help_as_visible_text(qapp: QApplication) -> None:
    del qapp
    registry = create_application_registry()
    session = DocumentSession(registry)
    node_id = session.add_node("synmachine.input.load_video", (0.0, 0.0))
    inspector = InspectorPanel(session)
    inspector.set_selection({node_id}, set())

    visible_help = {
        label.text()
        for label in inspector.findChildren(QLabel)
        if label.objectName().startswith("parameter_help_")
    }
    playback_speed = inspector.findChild(FloatRangeParameterEditor, "parameter_playback_speed")
    assert "Path to a saved video file." in visible_help
    assert any("N=2 processes source frames" in text for text in visible_help)
    assert any("Scale PTS playback timing from 0.25x to 4x" in text for text in visible_help)
    assert playback_speed is not None
    assert (playback_speed.minimum(), playback_speed.maximum(), playback_speed.value()) == (
        0.25,
        4.0,
        1.0,
    )
