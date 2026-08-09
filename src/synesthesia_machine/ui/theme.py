"""Central typed visual tokens shared by widgets and graphics items."""

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont


@dataclass(frozen=True, slots=True)
class ThemeColors:
    window: str = "#15181d"
    panel: str = "#1d2229"
    canvas: str = "#111419"
    grid: str = "#232a33"
    node: str = "#252b34"
    node_header: str = "#303845"
    border: str = "#46515f"
    text: str = "#e7ebf0"
    muted_text: str = "#9aa6b2"
    accent: str = "#61a8ff"
    selection: str = "#8cc2ff"
    warning: str = "#f0b44c"
    error: str = "#ef6b73"
    disabled: str = "#59616c"
    float_port: str = "#68b6ff"
    int_port: str = "#7ed6b3"
    bool_port: str = "#ef8f9c"
    string_port: str = "#d6a6ff"
    color_port: str = "#f5d76e"
    image_port: str = "#ff9b62"
    channel_port: str = "#63d5dc"
    midi_port: str = "#b7dd72"
    generic_port: str = "#aeb7c2"


@dataclass(frozen=True, slots=True)
class ThemeMetrics:
    node_width: float = 240.0
    header_height: float = 34.0
    row_height: float = 27.0
    node_radius: float = 7.0
    port_radius: float = 6.0
    cable_width: float = 2.5
    cable_hit_width: float = 12.0
    grid_size: float = 24.0
    canvas_margin: float = 80.0
    min_zoom: float = 0.2
    max_zoom: float = 3.0


@dataclass(frozen=True, slots=True)
class Theme:
    colors: ThemeColors = ThemeColors()
    metrics: ThemeMetrics = ThemeMetrics()
    body_points: float = 9.0
    title_points: float = 10.0

    def color(self, name: str) -> QColor:
        return QColor(getattr(self.colors, name))

    def body_font(self) -> QFont:
        font = QFont()
        font.setPointSizeF(self.body_points)
        return font

    def title_font(self) -> QFont:
        font = self.body_font()
        font.setPointSizeF(self.title_points)
        font.setBold(True)
        return font

    def style_sheet(self) -> str:
        colors = self.colors
        return f"""
            QMainWindow, QDialog {{ background: {colors.window}; color: {colors.text}; }}
            QWidget {{ color: {colors.text}; font-size: {self.body_points}pt; }}
            QDockWidget, QMenuBar, QMenu, QStatusBar {{ background: {colors.panel}; }}
            QWidget#inspector_panel, QScrollArea#inspector_scroll_area,
            QWidget#inspector_scroll_viewport, QWidget#inspector_form_container {{
                background: {colors.panel}; color: {colors.text}; }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTreeWidget,
            QPlainTextEdit {{ background: {colors.canvas}; border: 1px solid {colors.border};
                border-radius: 3px; padding: 4px; selection-background-color: {colors.accent}; }}
            QComboBox QAbstractItemView {{ background: {colors.canvas}; color: {colors.text};
                border: 1px solid {colors.border}; selection-background-color: {colors.accent};
                selection-color: {colors.canvas}; outline: 0; }}
            QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 3px 6px; }}
            QComboBox QAbstractItemView::item:selected {{ background: {colors.accent};
                color: {colors.canvas}; }}
            QAbstractItemView#parameter_choice_popup {{ background: {colors.canvas};
                color: {colors.text}; border: 1px solid {colors.border};
                selection-background-color: {colors.accent}; selection-color: {colors.canvas};
                outline: 0; }}
            QSlider::groove:horizontal {{ height: 4px; background: {colors.canvas};
                border: 1px solid {colors.border}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {colors.accent};
                border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: 8px; margin: -4px 0;
                background: {colors.text}; border: 1px solid {colors.accent};
                border-radius: 4px; }}
            QSlider::handle:horizontal:hover {{ background: {colors.selection}; }}
            QMenu::item {{ padding: 5px 24px 5px 9px; background: transparent; }}
            QMenu::item:selected {{ background: {colors.accent}; color: {colors.canvas}; }}
            QPushButton {{ background: {colors.node_header}; border: 1px solid {colors.border};
                border-radius: 3px; padding: 5px 9px; }}
            QPushButton:hover {{ background: {colors.border}; border-color: {colors.accent}; }}
            QPushButton:pressed {{ background: {colors.accent}; color: {colors.canvas}; }}
            QToolBar#transport_toolbar {{ background: {colors.panel}; spacing: 4px;
                border-bottom: 1px solid {colors.border}; padding: 3px; }}
            QToolBar#transport_toolbar QToolButton[transportControl="true"] {{
                background: {colors.node_header}; border: 1px solid {colors.border};
                border-radius: 4px; min-width: 54px; padding: 5px 9px; margin: 1px; }}
            QToolBar#transport_toolbar QToolButton[transportControl="true"]:hover {{
                background: {colors.border}; border-color: {colors.accent}; color: {colors.text}; }}
            QToolBar#transport_toolbar QToolButton[transportControl="true"]:pressed {{
                background: {colors.accent}; border-color: {colors.selection};
                color: {colors.canvas}; padding-top: 6px; padding-bottom: 4px; }}
            QToolBar#transport_toolbar QToolButton[transportControl="true"]:disabled {{
                background: {colors.panel}; border-color: {colors.grid};
                color: {colors.disabled}; }}
            QToolTip {{ background: {colors.node_header}; color: {colors.text};
                border: 1px solid {colors.border}; }}
        """


DEFAULT_THEME = Theme()


_NODE_CATEGORY_COLORS = {
    "Input": "#68b6ff",
    "Image / Adjustment": "#ffb55a",
    "Image / Analysis": "#ff8a80",
    "Image / Channel": "#63d5dc",
    "Image / Compositing": "#ffd166",
    "Image / Dimension": "#7ed6b3",
    "Image / Filter": "#b8e986",
    "Image / Utility": "#a8b3c7",
    "Synesthesia": "#ce93d8",
    "Utility": "#90a4ae",
    "Utility / Channel": "#80cbc4",
    "Utility / MIDI": "#c5e1a5",
    "Utility / Scalar": "#9fa8da",
    "Output / Audio": "#f48fb1",
    "Output / MIDI": "#b39ddb",
    "Visualization": "#ffcc80",
}


def node_category_color(category: str) -> QColor:
    """Return a calm, readable and stable color for one node-library group."""

    known = _NODE_CATEGORY_COLORS.get(category)
    if known is not None:
        return QColor(known)
    # Plugins can introduce categories without coordinating with the built-in palette.
    # A stable character sum keeps those colors repeatable between processes.
    hue = sum((index + 1) * ord(character) for index, character in enumerate(category)) % 360
    return QColor.fromHsv(hue, 105, 225)


def port_color_name(type_name: str) -> str:
    """Map stable type names to theme token names, not literal colours."""

    names = {
        "FLOAT": "float_port",
        "INT": "int_port",
        "BOOL": "bool_port",
        "STRING": "string_port",
        "COLOR": "color_port",
        "IMAGE": "image_port",
        "CHANNEL": "channel_port",
        "MIDI": "midi_port",
    }
    return names.get(type_name, "generic_port")
