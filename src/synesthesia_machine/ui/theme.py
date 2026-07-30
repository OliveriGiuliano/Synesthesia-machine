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
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTreeWidget,
            QPlainTextEdit {{ background: {colors.canvas}; border: 1px solid {colors.border};
                border-radius: 3px; padding: 4px; selection-background-color: {colors.accent}; }}
            QPushButton {{ background: {colors.node_header}; border: 1px solid {colors.border};
                border-radius: 3px; padding: 5px 9px; }}
            QPushButton:hover {{ border-color: {colors.accent}; }}
            QToolTip {{ background: {colors.node_header}; color: {colors.text};
                border: 1px solid {colors.border}; }}
        """


DEFAULT_THEME = Theme()


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
