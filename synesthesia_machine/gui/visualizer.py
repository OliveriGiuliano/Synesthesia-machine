"""
Note visualizer widget - displays a piano roll showing active notes.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QFont
from typing import Dict, Set, List
from synesthesia_machine.utils.musical_scales import get_note_name


class NoteVisualizer(QWidget):
    """
    Piano-roll style visualizer showing which notes are currently active.
    
    Displays a horizontal bar for each note in the scale, lighting up
    when that note is triggered.
    
    Signals:
        None
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_notes: Dict[int, int] = {}  # note -> velocity
        self._scale_notes: List[int] = list(range(60, 72))  # Default: C5-D6
        self._note_colors: Dict[int, QColor] = {}
        self._generate_colors()
        
        # Set minimum size (wider for multi-octave scales)
        self.setMinimumSize(600, 120)
        self.setStyleSheet("background-color: #1a1a2e; border: 1px solid #333;")
    
    def _generate_colors(self):
        """Generate distinct colors for each note based on its position in chromatic scale."""
        note_colors = [
            QColor(255, 80, 80),    # C - red
            QColor(255, 140, 80),   # C# - orange-red
            QColor(255, 200, 80),   # D - orange
            QColor(255, 255, 80),   # D# - yellow-orange
            QColor(200, 255, 80),   # E - yellow-green
            QColor(80, 255, 80),    # F - green
            QColor(80, 255, 180),   # F# - teal
            QColor(80, 200, 255),   # G - light blue
            QColor(80, 120, 255),   # G# - blue
            QColor(120, 80, 255),   # A - purple-blue
            QColor(180, 80, 255),   # A# - purple
            QColor(255, 80, 200),   # B - pink
        ]
        # Cover full MIDI range (0-127) so no notes fall back to gray
        for note in range(128):
            chromatic = note % 12
            self._note_colors[note] = note_colors[chromatic]
    
    def set_scale_notes(self, notes: List[int]):
        """Set the scale notes to display."""
        self._scale_notes = notes.copy()
        self.update()
    
    def update_active_notes(self, notes: Dict[int, int]):
        """
        Update the set of active notes and their velocities.
        
        Args:
            notes: Dictionary of {midi_note: velocity}
        """
        self._active_notes = notes.copy()
        self.update()
    
    def clear(self):
        """Clear all active notes."""
        self._active_notes.clear()
        self.update()
    
    def paintEvent(self, event):
        """Draw the visualizer."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # Background
        painter.fillRect(0, 0, width, height, QColor(26, 26, 46))
        
        if not self._scale_notes:
            painter.setPen(QColor(100, 100, 120))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No scale selected")
            return
        
        num_notes = len(self._scale_notes)
        margin = 10
        padding = 4
        
        # Calculate bar dimensions
        available_width = width - (2 * margin)
        available_height = height - (2 * margin)
        bar_width = max(20, (available_width - (num_notes - 1) * padding) / num_notes)
        total_width = num_notes * bar_width + (num_notes - 1) * padding
        start_x = margin + (available_width - total_width) // 2
        
        # Font size adapts to number of notes
        font_size = max(6, min(9, 240 // num_notes))

        # Draw each note bar
        for i, note in enumerate(self._scale_notes):
            x = start_x + i * (bar_width + padding)
            
            # Get velocity for this note
            velocity = self._active_notes.get(note, 0)
            
            # Bar background (dim)
            bar_height = available_height
            color = self._note_colors.get(note, QColor(100, 100, 100))
            
            if velocity > 0:
                # Active note: bright bar with height proportional to velocity
                active_height = (velocity / 127.0) * available_height
                y = margin + (available_height - active_height)

                # Glow effect
                painter.setPen(color.lighter(150))
                painter.setBrush(color)
                painter.drawRoundedRect(int(x), int(y), int(bar_width), int(active_height), 4, 4)

                # Brightness overlay based on velocity
                alpha = int(80 + (velocity / 127.0) * 75)
                overlay = QColor(color.red(), color.green(), color.blue(), alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(overlay)
                painter.drawRoundedRect(int(x), int(y), int(bar_width), int(active_height), 4, 4)

                # Note name
                painter.setPen(QColor(255, 255, 255))
                painter.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold))
                note_name = get_note_name(note)
                painter.drawText(
                    int(x), int(margin + active_height) + 14,
                    int(bar_width), 16,
                    Qt.AlignmentFlag.AlignCenter,
                    note_name
                )
            else:
                # Inactive note: dim outline
                painter.setPen(QColor(60, 60, 80))
                painter.setBrush(QColor(35, 35, 55))
                painter.drawRoundedRect(int(x), int(margin), int(bar_width), int(bar_height), 4, 4)
                
                # Note name (dim)
                painter.setPen(QColor(80, 80, 100))
                painter.setFont(QFont("Segoe UI", font_size))
                note_name = get_note_name(note)
                painter.drawText(
                    int(x), margin + int(bar_height) - 8,
                    int(bar_width), 16,
                    Qt.AlignmentFlag.AlignCenter,
                    note_name
                )
        
        painter.end()
    
    def resizeEvent(self, event):
        """Handle resize."""
        super().resizeEvent(event)
        self.update()