"""
Main application window for the Synesthesia Machine.

This class is a pure view - it handles UI construction, user interactions,
and visual feedback. All business logic (video capture, frame processing,
MIDI routing, audio synthesis) is delegated to SynesthesiaEngine.
"""

import os
import time
import cv2
import numpy as np
from typing import Dict, List, Optional
from collections import deque
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFileDialog,
    QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QStatusBar, QMessageBox, QScrollArea, QTextEdit,
    QSlider, QSplitter, QFrame
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap, QFont

from synesthesia_machine.core.engine import SynesthesiaEngine
from synesthesia_machine.synesthesia_modes.color_to_note import ColorToNoteMode
from synesthesia_machine.synesthesia_modes.brightness_to_pitch import BrightnessToPitchMode
from synesthesia_machine.gui.visualizer import NoteVisualizer
from synesthesia_machine.utils.musical_scales import (
    get_available_scales, get_note_name
)
from synesthesia_machine.config.settings import AppSettings


class MainWindow(QMainWindow):
    """Main application window - pure view layer."""
    
    def __init__(self):
        super().__init__()

        # Load persisted settings (or use defaults)
        self._settings = AppSettings.load_from_file()

        # Create and configure the engine (owns all core components)
        self._engine = SynesthesiaEngine(settings=self._settings)
        self._register_modes()
        
        # Performance tracking for UI display
        self._debug_enabled = False
        self._fps_history = deque(maxlen=60)
        self._frame_times = deque(maxlen=60)
        self._current_fps = 0.0
        self._avg_fps = 0.0
        self._avg_frame_time_ms = 0.0
        self._max_frame_time_ms = 0.0
        self._slow_frame_count = 0
        self._total_frame_count = 0
        self._FRAME_TIME_TARGET_MS = 16.67
        
        self._init_ui()
        self._connect_signals()
        self._apply_settings_to_ui()
        self._initialize_mode()
        self._update_scale_display()

    def _apply_settings_to_ui(self):
        """Restore UI controls from loaded settings."""
        # Volume
        volume_percent = int(self._settings.audio.volume * 100)
        self._volume_slider.setValue(volume_percent)
        self._volume_label.setText(f"{volume_percent}%")
        self._engine.set_volume(self._settings.audio.volume)

        # Mute
        self._mute_checkbox.setChecked(self._settings.audio.muted)
        self._engine.set_muted(self._settings.audio.muted)

        # Scale type
        scale_index = self._scale_combo.findData(self._settings.scale.scale_type)
        if scale_index >= 0:
            self._scale_combo.setCurrentIndex(scale_index)

        # MIDI output type
        midi_index = self._midi_type_combo.findData(self._settings.midi.output_type)
        if midi_index >= 0:
            self._midi_type_combo.setCurrentIndex(midi_index)
        self._engine.set_midi_output_type(self._settings.midi.output_type)

        # Mode
        mode_index = self._mode_combo.findData(self._settings.synesthesia.active_mode)
        if mode_index >= 0:
            self._mode_combo.setCurrentIndex(mode_index)
    
    def _register_modes(self):
        """Register all available synesthesia modes with the engine."""
        self._engine.register_mode("color_to_note", ColorToNoteMode)
        self._engine.register_mode("brightness_to_pitch", BrightnessToPitchMode)
    
    def _init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("Synesthesia Machine")
        self.setMinimumSize(1200, 800)
        self.resize(1500, 900)
        self.setStyleSheet(self._get_stylesheet())
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(6, 6, 6, 6)
        
        # Splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: transparent;
            }
            QSplitter::handle:hover {
                background: rgba(100, 134, 255, 0.4);
            }
        """)
        
        # Left panel - Video + Visualizer
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)
        
        # Right panel - Controls
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)
        
        # Set initial sizes (left ~75%, right ~25%)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        
        main_layout.addWidget(splitter)
        
        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready - Load a video file or connect a camera")
    
    def _create_debug_log(self) -> QTextEdit:
        """Create debug log display."""
        self._debug_log = QTextEdit()
        self._debug_log.setReadOnly(True)
        self._debug_log.setFont(QFont("Consolas", 9))
        self._debug_log.setMaximumHeight(120)
        self._debug_log.setStyleSheet("""
            QTextEdit {
                background-color: #111;
                color: #0f0;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 4px;
                font-family: Consolas, Courier, monospace;
                font-size: 9pt;
            }
        """)
        self._debug_log.setPlaceholderText("MIDI debug output...")
        return self._debug_log

    def _debug_print(self, message: str):
        """Add a message to the debug log with timestamp (only if debug enabled)."""
        if not self._debug_enabled:
            return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._debug_log.append(f"[{ts}] {message}")
        # Keep only last 50 lines by rebuilding content
        doc = self._debug_log.document()
        if doc.blockCount() > 50:
            text = self._debug_log.toPlainText()
            lines = text.split('\n')
            lines = lines[-50:]
            self._debug_log.setPlainText('\n'.join(lines))
            scrollbar = self._debug_log.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())
    
    def _on_debug_toggled(self, enabled: bool):
        """Handle debug mode toggle."""
        self._debug_enabled = enabled
        self._debug_widget.setVisible(enabled)
        if enabled:
            self._debug_print("Debug enabled")
        else:
            self._debug_log.clear()
    
    def _update_performance_display(self, frame_time_ms: float):
        """Update FPS and performance metrics display.
        
        Args:
            frame_time_ms: Processing time of the current frame in milliseconds.
        """
        now = time.time()
        self._fps_history.append(now)
        self._frame_times.append(frame_time_ms)
        self._total_frame_count += 1
        
        if frame_time_ms > self._FRAME_TIME_TARGET_MS:
            self._slow_frame_count += 1
        
        if frame_time_ms > self._max_frame_time_ms:
            self._max_frame_time_ms = frame_time_ms
        
        if len(self._fps_history) > 1:
            time_span = self._fps_history[-1] - self._fps_history[0]
            if time_span > 0:
                self._current_fps = len(self._fps_history) / time_span
        
        if self._frame_times:
            self._avg_frame_time_ms = sum(self._frame_times) / len(self._frame_times)
            self._avg_fps = 1000.0 / self._avg_frame_time_ms if self._avg_frame_time_ms > 0 else 0
        
        slow_pct = (self._slow_frame_count / self._total_frame_count * 100) if self._total_frame_count > 0 else 0.0
        
        self._fps_label.setText(f"FPS: {self._current_fps:.1f}")
        self._frame_time_label.setText(f"Frame: {frame_time_ms:.1f} ms")
        self._avg_fps_label.setText(f"Avg: {self._avg_fps:.1f} fps")
        self._max_frame_label.setText(f"Max: {self._max_frame_time_ms:.1f} ms")
        self._slow_frame_label.setText(f"Slow: {self._slow_frame_count} ({slow_pct:.1f}%)")
        
        # Color code FPS label
        if self._current_fps >= 60:
            self._fps_label.setStyleSheet("color: #9ece6a; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        elif self._current_fps >= 30:
            self._fps_label.setStyleSheet("color: #e0af68; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        else:
            self._fps_label.setStyleSheet("color: #f7768e; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        
        # Color code slow frame label
        if slow_pct <= 5:
            self._slow_frame_label.setStyleSheet("color: #9ece6a; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        elif slow_pct <= 20:
            self._slow_frame_label.setStyleSheet("color: #e0af68; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        else:
            self._slow_frame_label.setStyleSheet("color: #f7768e; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")

    def _create_left_panel(self) -> QWidget:
        """Create the left panel with video display and visualizer."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(6)
        
        # Debug panel (collapsible)
        debug_group = QGroupBox("")
        debug_layout = QVBoxLayout()
        
        # Debug controls row
        debug_controls = QHBoxLayout()
        self._debug_checkbox = QCheckBox("Enable Debug")
        self._debug_checkbox.toggled.connect(self._on_debug_toggled)
        debug_controls.addWidget(self._debug_checkbox)
        
        # FPS display
        self._fps_label = QLabel("FPS: --")
        self._fps_label.setStyleSheet("color: #0f0; font-family: monospace; font-size: 11px; padding: 0 8px;")
        debug_controls.addWidget(self._fps_label)
        
        # Frame time display
        self._frame_time_label = QLabel("Frame: -- ms")
        self._frame_time_label.setStyleSheet("color: #ff0; font-family: monospace; font-size: 11px; padding: 0 8px;")
        debug_controls.addWidget(self._frame_time_label)
        
        # Avg FPS display
        self._avg_fps_label = QLabel("Avg: -- fps")
        self._avg_fps_label.setStyleSheet("color: #7dcfff; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        debug_controls.addWidget(self._avg_fps_label)
        
        # Max frame time display
        self._max_frame_label = QLabel("Max: -- ms")
        self._max_frame_label.setStyleSheet("color: #f7768e; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        debug_controls.addWidget(self._max_frame_label)
        
        # Slow frame count display
        self._slow_frame_label = QLabel("Slow: 0 (0.0%)")
        self._slow_frame_label.setStyleSheet("color: #9ece6a; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; padding: 0 8px;")
        debug_controls.addWidget(self._slow_frame_label)
        
        debug_controls.addStretch()
        debug_layout.addLayout(debug_controls)
        
        # Debug log (hidden by default)
        self._debug_widget = self._create_debug_log()
        self._debug_widget.setVisible(False)
        debug_layout.addWidget(self._debug_widget)
        
        debug_group.setLayout(debug_layout)
        layout.addWidget(debug_group, stretch=0)
        
        # Video display
        video_group = QGroupBox("")
        video_layout = QVBoxLayout()
        
        self._video_label = QLabel("No video source")
        self._video_label.setMinimumSize(640, 360)
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setStyleSheet(
            "color: #888; background-color: #111; "
            "border: 2px solid #333; border-radius: 4px;"
        )
        video_layout.addWidget(self._video_label)
        
        # Video controls
        controls_layout = QHBoxLayout()
        
        self._btn_load_file = QPushButton("📁 Load File")
        self._btn_camera = QPushButton("📷 Connect Camera")
        self._btn_play = QPushButton("▶ Play")
        self._btn_pause = QPushButton("⏸ Pause")
        self._btn_stop = QPushButton("⏹ Stop")
        
        for btn in [self._btn_load_file, self._btn_camera,
                     self._btn_play, self._btn_pause, self._btn_stop]:
            btn.setMaximumHeight(28)
        
        controls_layout.addWidget(self._btn_load_file)
        controls_layout.addWidget(self._btn_camera)
        controls_layout.addStretch()
        controls_layout.addWidget(self._btn_play)
        controls_layout.addWidget(self._btn_pause)
        controls_layout.addWidget(self._btn_stop)
        
        # Seek slider
        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setMinimum(0)
        self._seek_slider.setMaximum(1000)
        self._seek_slider.setValue(0)
        self._seek_slider.setEnabled(False)
        video_layout.addWidget(self._seek_slider)
        
        # Time display
        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setStyleSheet("color: #888; font-size: 12px;")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        video_layout.addWidget(self._time_label)
        
        video_layout.addLayout(controls_layout)
        video_group.setLayout(video_layout)
        layout.addWidget(video_group, stretch=2)
        
        # Visualizer
        viz_group = QGroupBox("")
        viz_layout = QVBoxLayout()
        
        self._visualizer = NoteVisualizer()
        viz_layout.addWidget(self._visualizer)
        viz_group.setLayout(viz_layout)
        layout.addWidget(viz_group, stretch=1)
        
        return panel
    
    def _create_right_panel(self) -> QWidget:
        """Create the right panel with two distinct group-box panels: mode controls (top) and general controls (bottom)."""
        panel = QWidget()
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setSpacing(6)
        
        # --- TOP PANEL: Synesthesia Mode Controls ---
        mode_group = QGroupBox("🎨  Synesthesia Mode")
        mode_group.setObjectName("mode_panel")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(6)
        
        # Mode combo
        mode_combo_frame = QFrame()
        mode_combo_frame.setObjectName("mode_combo_frame")
        mode_combo_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mode_combo_frame.setStyleSheet("""
            #mode_combo_frame {
                background-color: #151620;
                border: 2px solid #445588;
                border-radius: 8px;
                padding: 2px 4px;
            }
            #mode_combo_frame:hover {
                border: 2px solid #7aa2f7;
            }
        """)
        mode_combo_layout = QHBoxLayout(mode_combo_frame)
        mode_combo_layout.setContentsMargins(4, 2, 4, 2)
        mode_combo_layout.setSpacing(0)
        
        self._mode_combo = QComboBox()
        self._mode_combo.setToolTip("Click to choose a synesthesia mode")
        self._mode_combo.setObjectName("mode_combo")
        self._mode_combo.setFrame(False)
        self._mode_combo.setStyleSheet("""
            #mode_combo {
                background: transparent;
                border: none;
                color: #c0caf5;
                font-size: 12px;
                font-weight: 500;
            }
        """)
        for mode_id, mode_class in self._engine.get_available_modes().items():
            temp = mode_class()
            self._mode_combo.addItem(temp.get_name(), mode_id)
            temp.deleteLater()
        mode_combo_layout.addWidget(self._mode_combo)
        mode_layout.addWidget(mode_combo_frame)
        
        # Mode description
        self._mode_desc_label = QLabel()
        self._mode_desc_label.setStyleSheet("color: #999; font-size: 11px;")
        self._mode_desc_label.setWordWrap(True)
        mode_layout.addWidget(self._mode_desc_label)
        
        # Mode parameters
        self._mode_params_widget = QWidget()
        self._mode_params_layout = QVBoxLayout(self._mode_params_widget)
        self._mode_params_layout.setSpacing(4)
        mode_layout.addWidget(self._mode_params_widget)
        
        layout.addWidget(mode_group, stretch=0)
        
        # --- BOTTOM PANEL: General Controls ---
        general_group = QGroupBox("⚙  General Controls")
        general_group.setObjectName("general_panel")
        general_layout = QVBoxLayout(general_group)
        general_layout.setSpacing(8)
        
        # Volume & Mute
        vol_header = QLabel("🔊  Audio")
        vol_header.setStyleSheet("color: #7aa2f7; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; padding: 2px 0;")
        general_layout.addWidget(vol_header)
        
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("Volume:"))
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setMinimum(0)
        self._volume_slider.setMaximum(100)
        self._volume_slider.setValue(75)
        vol_layout.addWidget(self._volume_slider)
        self._volume_label = QLabel("75%")
        self._volume_label.setMinimumWidth(35)
        vol_layout.addWidget(self._volume_label)
        general_layout.addLayout(vol_layout)
        
        self._mute_checkbox = QCheckBox("Mute")
        general_layout.addWidget(self._mute_checkbox)
        
        # Musical Scale
        scale_header = QLabel("🎵  Musical Scale")
        scale_header.setStyleSheet("color: #7aa2f7; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; padding: 2px 0;")
        general_layout.addWidget(scale_header)
        
        scale_type_layout = QHBoxLayout()
        scale_type_layout.addWidget(QLabel("Scale:"))
        
        scale_combo_frame = QFrame()
        scale_combo_frame.setObjectName("scale_combo_frame")
        scale_combo_frame.setFrameShape(QFrame.Shape.StyledPanel)
        scale_combo_frame.setStyleSheet("""
            #scale_combo_frame {
                background-color: #151620;
                border: 2px solid #445588;
                border-radius: 8px;
                padding: 2px 4px;
            }
            #scale_combo_frame:hover {
                border: 2px solid #7aa2f7;
            }
        """)
        scale_combo_layout = QHBoxLayout(scale_combo_frame)
        scale_combo_layout.setContentsMargins(4, 2, 4, 2)
        scale_combo_layout.setSpacing(0)
        
        self._scale_combo = QComboBox()
        self._scale_combo.setToolTip("Click to choose a musical scale")
        self._scale_combo.setObjectName("scale_combo")
        self._scale_combo.setFrame(False)
        self._scale_combo.setStyleSheet("""
            #scale_combo {
                background: transparent;
                border: none;
                color: #c0caf5;
                font-size: 12px;
                font-weight: 500;
            }
        """)
        for scale_name in get_available_scales():
            display = scale_name.replace("_", " ").title()
            self._scale_combo.addItem(display, scale_name)
        scale_combo_layout.addWidget(self._scale_combo)
        scale_type_layout.addWidget(scale_combo_frame)
        general_layout.addLayout(scale_type_layout)
        
        # Note range
        range_layout = QHBoxLayout()
        range_layout.setSpacing(6)
        range_layout.addWidget(QLabel("Range:"))
        
        min_frame = QFrame()
        min_frame.setObjectName("min_note_frame")
        min_frame.setFrameShape(QFrame.Shape.StyledPanel)
        min_frame.setStyleSheet("""
            #min_note_frame {
                background-color: #151620;
                border: 2px solid #6644aa;
                border-radius: 8px;
                padding: 2px 4px;
            }
            #min_note_frame:hover {
                border: 2px solid #a855f7;
            }
        """)
        min_frame_layout = QHBoxLayout(min_frame)
        min_frame_layout.setContentsMargins(4, 2, 4, 2)
        min_frame_layout.setSpacing(0)
        
        self._min_note_spin = QSpinBox()
        self._min_note_spin.setMinimum(0)
        self._min_note_spin.setMaximum(127)
        self._min_note_spin.setValue(24)
        self._min_note_spin.setFixedWidth(44)
        self._min_note_spin.setStyleSheet("""
            background: transparent;
            border: none;
            color: #c0caf5;
            font-size: 12px;
            font-weight: 500;
            text-align: center;
        """)
        min_frame_layout.addWidget(self._min_note_spin)
        
        max_frame = QFrame()
        max_frame.setObjectName("max_note_frame")
        max_frame.setFrameShape(QFrame.Shape.StyledPanel)
        max_frame.setStyleSheet("""
            #max_note_frame {
                background-color: #151620;
                border: 2px solid #6644aa;
                border-radius: 8px;
                padding: 2px 4px;
            }
            #max_note_frame:hover {
                border: 2px solid #a855f7;
            }
        """)
        max_frame_layout = QHBoxLayout(max_frame)
        max_frame_layout.setContentsMargins(4, 2, 4, 2)
        max_frame_layout.setSpacing(0)
        
        self._max_note_spin = QSpinBox()
        self._max_note_spin.setMinimum(0)
        self._max_note_spin.setMaximum(127)
        self._max_note_spin.setValue(96)
        self._max_note_spin.setFixedWidth(44)
        self._max_note_spin.setStyleSheet("""
            background: transparent;
            border: none;
            color: #c0caf5;
            font-size: 12px;
            font-weight: 500;
            text-align: center;
        """)
        max_frame_layout.addWidget(self._max_note_spin)
        
        range_layout.addWidget(min_frame)
        range_layout.addWidget(max_frame)
        general_layout.addLayout(range_layout)
        
        # MIDI Output
        midi_header = QLabel("🎹  MIDI Output")
        midi_header.setStyleSheet("color: #7aa2f7; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; padding: 2px 0;")
        midi_header.setToolTip("Select MIDI output device")
        general_layout.addWidget(midi_header)
        
        midi_combo_frame = QFrame()
        midi_combo_frame.setObjectName("midi_combo_frame")
        midi_combo_frame.setFrameShape(QFrame.Shape.StyledPanel)
        midi_combo_frame.setStyleSheet("""
            #midi_combo_frame {
                background-color: #151620;
                border: 2px solid #445588;
                border-radius: 8px;
                padding: 2px 4px;
            }
            #midi_combo_frame:hover {
                border: 2px solid #7aa2f7;
            }
        """)
        midi_combo_layout = QHBoxLayout(midi_combo_frame)
        midi_combo_layout.setContentsMargins(4, 2, 4, 2)
        midi_combo_layout.setSpacing(0)
        
        self._midi_type_combo = QComboBox()
        self._midi_type_combo.setToolTip("Click to choose MIDI output device")
        self._midi_type_combo.setObjectName("midi_combo")
        self._midi_type_combo.setFrame(False)
        self._midi_type_combo.setStyleSheet("""
            #midi_combo {
                background: transparent;
                border: none;
                color: #c0caf5;
                font-size: 12px;
                font-weight: 500;
            }
        """)
        self._midi_type_combo.addItem("Internal (PyGame)", "internal")
        midi_combo_layout.addWidget(self._midi_type_combo)
        general_layout.addWidget(midi_combo_frame)
        
        self._btn_refresh_midi = QPushButton("🔄 Refresh Devices")
        self._btn_refresh_midi.setMaximumHeight(28)
        general_layout.addWidget(self._btn_refresh_midi)
        
        # Frame Rate — compact "1 / N" display
        framerate_header = QLabel("⏱  Frame Rate")
        framerate_header.setStyleSheet("color: #7aa2f7; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; padding: 2px 0;")
        general_layout.addWidget(framerate_header)
        
        framerate_layout = QHBoxLayout()
        framerate_layout.addWidget(QLabel("1 /"))
        
        framerate_spin_frame = QFrame()
        framerate_spin_frame.setObjectName("framerate_spin_frame")
        framerate_spin_frame.setFrameShape(QFrame.Shape.StyledPanel)
        framerate_spin_frame.setStyleSheet("""
            #framerate_spin_frame {
                background-color: #151620;
                border: 2px solid #6644aa;
                border-radius: 8px;
                padding: 1px 3px;
            }
            #framerate_spin_frame:hover {
                border: 2px solid #a855f7;
            }
        """)
        framerate_spin_layout = QHBoxLayout(framerate_spin_frame)
        framerate_spin_layout.setContentsMargins(3, 1, 3, 1)
        framerate_spin_layout.setSpacing(0)
        
        self._framerate_spin = QSpinBox()
        self._framerate_spin.setToolTip("Process 1 out of N frames (1 = all frames)")
        self._framerate_spin.setMinimum(1)
        self._framerate_spin.setMaximum(100)
        self._framerate_spin.setValue(1)
        self._framerate_spin.setFixedWidth(40)
        self._framerate_spin.setStyleSheet("""
            QSpinBox {
                background: transparent;
                border: none;
                color: #c0caf5;
                font-size: 12px;
                font-weight: 500;
                text-align: center;
                padding: 0px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0;
                height: 0;
                border: none;
                background: none;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow {
                image: none;
            }
        """)
        framerate_spin_layout.addWidget(self._framerate_spin)
        framerate_layout.addWidget(framerate_spin_frame)
        framerate_layout.addStretch()
        general_layout.addLayout(framerate_layout)
        
        general_layout.addStretch()
        layout.addWidget(general_group, stretch=1)
        
        return panel
    
    def _connect_signals(self):
        """Connect all UI signals and engine signals."""
        # Video controls
        self._btn_load_file.clicked.connect(self._load_video_file)
        self._btn_camera.clicked.connect(self._connect_camera)
        self._btn_play.clicked.connect(self._start_playback)
        self._btn_pause.clicked.connect(self._pause_playback)
        self._btn_stop.clicked.connect(self._stop_playback)
        self._seek_slider.sliderReleased.connect(self._seek_released)
        
        # Video source signals (via engine)
        self._engine.video_source.frame_ready.connect(self._on_frame_ready)
        self._engine.video_source.position_changed.connect(self._on_position_changed)
        self._engine.video_source.playback_ended.connect(self._on_playback_ended)
        self._engine.video_source.error_occurred.connect(self._on_video_error)
        
        # Engine signals
        self._engine.active_notes_changed.connect(self._on_active_notes_changed)
        self._engine.frame_processed.connect(self._update_performance_display)
        self._engine.error_occurred.connect(self._on_engine_error)
        
        # MIDI signals (via engine)
        self._engine.midi_output.devices_updated.connect(self._on_midi_devices_updated)
        self._engine.midi_output.error_occurred.connect(self._on_midi_error)
        
        # Audio engine signals (via engine)
        self._engine.audio_engine.notes_changed.connect(self._on_audio_notes_changed)
        
        # Mode selection
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        
        # Scale
        self._scale_combo.currentIndexChanged.connect(self._update_scale_display)
        self._min_note_spin.valueChanged.connect(self._update_scale_display)
        self._max_note_spin.valueChanged.connect(self._update_scale_display)
        
        # Audio
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._mute_checkbox.toggled.connect(self._on_mute_toggled)
        
        # MIDI output type
        self._midi_type_combo.currentIndexChanged.connect(self._on_midi_type_changed)
        
        # MIDI refresh
        self._btn_refresh_midi.clicked.connect(self._refresh_midi_devices)
        
        # Frame rate
        self._framerate_spin.valueChanged.connect(self._on_framerate_changed)
    
    def _initialize_mode(self):
        """Initialize the synesthesia mode. Use the currently selected combo
        index (which may have been restored from saved settings), falling back
        to the first available mode if nothing is selected."""
        available = self._engine.get_available_modes()
        if not available:
            return
        
        # Use the mode currently selected in the combo box (set by _apply_settings_to_ui),
        # or fall back to the first available mode
        mode_id = self._mode_combo.itemData(self._mode_combo.currentIndex())
        if mode_id is None or mode_id not in available:
            mode_id = list(available.keys())[0]
            self._mode_combo.setCurrentIndex(self._mode_combo.findData(mode_id))
        
        self._engine.initialize_mode(mode_id)
        self._populate_mode_parameters()
        self._update_mode_description()
    
    def _create_bordered_spin_frame(self, spinner):
        """Wrap a spin box in a QFrame for reliable border rendering."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("""
            background-color: #151620;
            border: 2px solid #6644aa;
            border-radius: 8px;
            padding: 2px 4px;
        """)
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(4, 2, 4, 2)
        frame_layout.setSpacing(0)
        spinner.setStyleSheet("""
            background: transparent;
            border: none;
            color: #c0caf5;
            font-size: 12px;
            font-weight: 500;
            text-align: center;
        """)
        frame_layout.addWidget(spinner)
        return frame

    def _populate_mode_parameters(self):
        """Populate the mode-specific parameters UI."""
        # Clear existing - handle both widgets and nested layouts
        while self._mode_params_layout.count():
            child = self._mode_params_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                # Recursively delete widgets in nested layout (our QHBoxLayout rows)
                nested = child.layout()
                while nested.count():
                    nested_child = nested.takeAt(0)
                    if nested_child.widget():
                        nested_child.widget().deleteLater()
                nested.deleteLater()
        
        mode = self._engine.current_mode
        if mode is None:
            return
        
        params = mode.get_parameters()
        for param_name, param_value in params.items():
            row = QHBoxLayout()
            row.setSpacing(6)
            
            label = QLabel(param_name.replace("_", " ").title() + ":")
            label.setStyleSheet("color: #bbb; font-size: 11px;")
            label.setMinimumWidth(100)
            row.addWidget(label)
            
            if param_name == "activation_threshold":
                spinner = QDoubleSpinBox()
                spinner.setMinimum(0.0)
                spinner.setMaximum(100.0)
                spinner.setSingleStep(1.0)
                spinner.setDecimals(1)
                spinner.setSuffix(" %")
                spinner.setValue(param_value)
                spinner.setObjectName(param_name)
                spinner.valueChanged.connect(
                    lambda v, n=param_name: self._on_mode_param_changed(n, v)
                )
                row.addWidget(self._create_bordered_spin_frame(spinner))
            elif isinstance(param_value, int):
                spinner = QSpinBox()
                spinner.setMinimum(0)
                spinner.setMaximum(10000)
                spinner.setValue(param_value)
                spinner.setObjectName(param_name)
                spinner.valueChanged.connect(
                    lambda v, n=param_name: self._on_mode_param_changed(n, v)
                )
                row.addWidget(self._create_bordered_spin_frame(spinner))
            elif isinstance(param_value, float):
                spinner = QDoubleSpinBox()
                spinner.setMinimum(0.1)
                spinner.setMaximum(10.0)
                spinner.setSingleStep(0.1)
                spinner.setValue(param_value)
                spinner.setObjectName(param_name)
                spinner.valueChanged.connect(
                    lambda v, n=param_name: self._on_mode_param_changed(n, v)
                )
                row.addWidget(self._create_bordered_spin_frame(spinner))
            
            self._mode_params_layout.addLayout(row)
    
    def _update_mode_description(self):
        """Update the mode description label."""
        mode = self._engine.current_mode
        if mode:
            self._mode_desc_label.setText(mode.get_description())
    
    def _update_scale_display(self):
        """Apply scale to visualizer and engine."""
        scale_type = self._scale_combo.currentData()
        root_note = 60  # C5 default
        min_note = self._min_note_spin.value()
        max_note = self._max_note_spin.value()
        
        notes = self._engine.get_scale_notes()
        self._engine.set_scale(scale_type, root_note, min_note, max_note)
        
        # Recompute notes after engine scale update
        notes = self._engine.get_scale_notes()
        self._visualizer.set_scale_notes(notes)
    
    # --- Video handling ---
    
    def _load_video_file(self):
        """Open a file dialog to load a video."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video File",
            "",
            "Video Files (*.mp4 *.avi *.mov *.mkv *.webm *.flv *.wmv);;All Files (*.*)"
        )
        if file_path:
            if self._engine.video_source.open_file(file_path):
                self._engine.settings.video.file_path = file_path
                self._engine.settings.video.source_type = "file"
                self._status_bar.showMessage(f"Loaded: {os.path.basename(file_path)}")
                self._seek_slider.setEnabled(True)
                self._engine.video_source.start()
                self._engine.start_processing()
            else:
                QMessageBox.warning(self, "Error", "Failed to open video file.")
    
    def _connect_camera(self):
        """Connect to a camera device."""
        indices = [0, 1, 2]
        for idx in indices:
            if self._engine.video_source.open_camera(idx):
                self._engine.settings.video.source_type = "camera"
                self._engine.settings.video.camera_index = idx
                self._status_bar.showMessage(f"Camera {idx} connected")
                self._seek_slider.setEnabled(False)
                self._engine.video_source.start()
                self._engine.start_processing()
                return
        QMessageBox.warning(self, "Error", "No camera device found.")
    
    def _start_playback(self):
        if not self._engine.video_source.is_playing:
            if self._engine.is_running:
                self._engine.video_source.resume()
                self._status_bar.showMessage("Playing")
            else:
                self._status_bar.showMessage("No video source loaded")
    
    def _pause_playback(self):
        self._engine.video_source.pause()
        self._status_bar.showMessage("Paused")
    
    def _stop_playback(self):
        self._engine.video_source.stop()
        self._engine.stop_processing()
        self._visualizer.clear()
        self._video_label.clear()
        self._video_label.setText("No video source")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setStyleSheet(
            "color: #888; background-color: #111; "
            "border: 2px solid #333; border-radius: 4px;"
        )
        self._status_bar.showMessage("Stopped")
    
    def _seek_released(self):
        """Actually perform the seek when user releases the slider."""
        if self._engine.video_source.source_type == "file":
            total = self._engine.video_source.get_total_frames()
            if total > 0:
                value = self._seek_slider.value()
                self._engine.video_source.seek_to_frame(value)
    
    def _on_frame_ready(self, frame_rgb: np.ndarray):
        """Called when a new frame is available from the video source."""
        try:
            # Display the frame
            h, w, ch = frame_rgb.shape
            q_image = QImage(frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(q_image)
            self._video_label.setPixmap(
                pixmap.scaled(
                    self._video_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation
                )
            )
        except Exception as e:
            self._status_bar.showMessage(f"Frame display error: {e}")
    
    def _on_position_changed(self, current: int, total: int):
        """Update seek slider and time display."""
        if total > 0:
            self._seek_slider.setMaximum(total)
            self._seek_slider.setValue(current)
            
            fps = self._engine.video_source.get_fps()
            current_time = current / fps if fps > 0 else 0
            total_time = total / fps if fps > 0 else 0
            self._time_label.setText(
                f"{self._format_time(current_time)} / {self._format_time(total_time)}"
            )
    
    def _on_playback_ended(self):
        """Called when video playback reaches the end."""
        self._stop_playback()
        self._status_bar.showMessage("Playback ended")
    
    def _on_video_error(self, message: str):
        """Handle video source errors."""
        self._status_bar.showMessage(f"Video error: {message}")
        QMessageBox.warning(self, "Video Error", message)
    
    def _on_engine_error(self, message: str):
        """Handle engine errors."""
        self._status_bar.showMessage(message)
    
    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"
    
    # --- Engine signal handlers ---
    
    def _on_active_notes_changed(self, active_notes: Dict[int, int]):
        """Update visualizer when engine reports active notes change."""
        self._visualizer.update_active_notes(active_notes)
    
    def _on_audio_notes_changed(self, active_notes):
        """Called when audio engine active notes change (note ended naturally)."""
        notes_list = sorted(active_notes) if active_notes else []
        self._debug_print(f"AUDIO active notes changed: {notes_list}")
    
    # --- Mode handling ---
    
    def _on_mode_changed(self, index: int):
        """Handle synesthesia mode selection change."""
        mode_id = self._mode_combo.itemData(index)
        self._settings.synesthesia.active_mode = mode_id
        self._engine.switch_mode(mode_id)
        self._populate_mode_parameters()
        self._update_mode_description()
        mode = self._engine.current_mode
        if mode:
            self._status_bar.showMessage(f"Mode: {mode.get_name()}")
    
    def _on_mode_param_changed(self, param_name: str, value):
        """Handle mode parameter change."""
        setattr(self._settings.synesthesia, param_name, value)
        self._engine.update_mode_parameter(param_name, value)
    
    # --- MIDI handling ---
    
    def _on_midi_devices_updated(self, devices):
        """Update MIDI device list in the combo box."""
        current = self._midi_type_combo.currentIndex()
        self._midi_type_combo.clear()
        self._midi_type_combo.addItem("Internal (PyGame)", "internal")
        for idx, name in devices:
            self._midi_type_combo.addItem(f"{name} [{idx}]", f"device:{idx}")
        self._midi_type_combo.setCurrentIndex(current)
    
    def _on_midi_error(self, message: str):
        self._status_bar.showMessage(f"MIDI error: {message}")
    
    def _refresh_midi_devices(self):
        self._engine.refresh_midi_devices()
    
    def _on_midi_type_changed(self, index: int):
        """Handle MIDI output type change."""
        output_type = self._midi_type_combo.itemData(index)
        self._settings.midi.output_type = output_type
        self._engine.set_midi_output_type(output_type)
    
    def _on_framerate_changed(self, value: int):
        """Handle frame rate (frame skip) change.
        
        Spin value of N means process 1 out of N frames.
        Frame skip = N - 1 (so value 1 = skip 0 = all frames).
        """
        self._engine.set_frame_skip(value - 1)
    
    # --- Audio handling ---
    
    def _on_volume_changed(self, value: int):
        volume = value / 100.0
        self._volume_label.setText(f"{value}%")
        self._settings.audio.volume = volume
        self._engine.set_volume(volume)
    
    def _on_mute_toggled(self, muted: bool):
        self._settings.audio.muted = muted
        self._engine.set_muted(muted)
    
    # --- Debug ---
    
    def _send_event_debug(self, event):
        """Debug logging for note events."""
        state = "ON" if event.is_on else "OFF"
        vel_str = f" vel={event.velocity}" if event.is_on else ""
        self._debug_print(
            f"NOTE {state} ch=1 note={event.note} ({get_note_name(event.note)})"
            f"{vel_str} | active: {sorted(self._engine.audio_engine.active_notes)}"
        )
    
    # --- Styling ---
    
    def _get_stylesheet(self) -> str:
        return """
            /* === Window & Background === */
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1b26,
                    stop:0.5 #181924,
                    stop:1 #151620);
            }

            /* === Group Boxes — Floating Glass Cards === */
            QGroupBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30,32,48,0.85),
                    stop:1 rgba(24,26,40,0.85));
                border: 1.5px solid rgba(60, 65, 90, 0.5);
                border-radius: 12px;
                margin-top: 8px;
                padding-top: 16px;
                padding-bottom: 8px;
                font-size: 13px;
                font-weight: 600;
                color: #c0caf5;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px 4px 6px;
                color: #7aa2f7;
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 1.2px;
            }

            /* === Labels === */
            QLabel {
                color: #a9b1d6;
                font-size: 12px;
                font-weight: 500;
            }

            /* === Combo Boxes — Pill Dropdowns === */
            QComboBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(21,22,30,0.9),
                    stop:1 rgba(18,19,27,0.9));
                color: #c0caf5;
                border: 2px solid #6486ff;
                border-radius: 10px;
                padding: 5px 10px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 500;
            }

            /* Named combo boxes (mode, scale, midi) — setFrame(False) needs explicit border */
            QComboBox#mode_combo,
            QComboBox#scale_combo,
            QComboBox#midi_combo {
                background-color: #151620;
                border: 2px solid #445588;
                border-radius: 8px;
            }
            QComboBox#mode_combo:hover,
            QComboBox#scale_combo:hover,
            QComboBox#midi_combo:hover {
                border: 2px solid #7aa2f7;
            }
            QComboBox#mode_combo:focus,
            QComboBox#scale_combo:focus,
            QComboBox#midi_combo:focus {
                border: 2.5px solid #6486ff;
            }
            QComboBox:hover {
                border: 2px solid #7aa2f7;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(25,26,36,0.95),
                    stop:1 rgba(22,23,32,0.95));
            }
            QComboBox:focus {
                border: 2.5px solid #6486ff;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
                outline: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #7aa2f7;
                margin-right: 6px;
            }
            QComboBox::popup {
                background: #1a1b26;
                border: 1.5px solid rgba(60, 65, 90, 0.5);
                border-radius: 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1b26;
                background: #1a1b26;
                background-attachment: fixed;
                border: 1.5px solid rgba(60, 65, 90, 0.5);
                border-radius: 10px;
                color: #c0caf5;
                selection-background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6486ff,
                    stop:1 #a855f7);
                selection-color: #ffffff;
                outline: none;
                padding: 4px;
                gridline-color: rgba(60, 65, 90, 0.3);
            }
            QComboBox QAbstractItemView::item {
                min-height: 28px;
                padding: 4px 10px;
                border-radius: 6px;
                background-color: #1a1b26;
                background: #1a1b26;
                color: #c0caf5;
                border-bottom: 1px solid rgba(60, 65, 90, 0.2);
            }
            QComboBox QAbstractItemView::item:last {
                border-bottom: none;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #6486ff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6486ff,
                    stop:1 #a855f7);
                color: #ffffff;
                font-weight: 600;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: rgba(122, 162, 247, 0.15);
                background: rgba(122, 162, 247, 0.15);
                color: #c0caf5;
            }
            QListView {
                background-color: #1a1b26;
                background: #1a1b26;
                color: #c0caf5;
                selection-background-color: #6486ff;
                selection-color: #ffffff;
                outline: none;
                border: 1.5px solid rgba(60, 65, 90, 0.5);
                border-radius: 10px;
            }
            QListView::item {
                background-color: #1a1b26;
                color: #c0caf5;
            }
            QListView::item:selected {
                background-color: #6486ff;
                color: #ffffff;
            }
            QListView::item:hover {
                background-color: rgba(122, 162, 247, 0.15);
                color: #c0caf5;
            }

            /* === Spin Boxes — Glass Inputs (no arrows) === */
            QSpinBox, QDoubleSpinBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(21,22,30,0.9),
                    stop:1 rgba(18,19,27,0.9));
                color: #c0caf5;
                border: 2px solid #a855f7;
                border-radius: 10px;
                padding: 4px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 500;
            }

            /* Named spin boxes (min/max note) — explicit solid border */
            QSpinBox#min_note_spin,
            QSpinBox#max_note_spin {
                background-color: #151620;
                border: 2px solid #6644aa;
                border-radius: 8px;
            }
            QSpinBox#min_note_spin:hover,
            QSpinBox#max_note_spin:hover {
                border: 2px solid #a855f7;
            }
            QSpinBox#min_note_spin:focus,
            QSpinBox#max_note_spin:focus {
                border: 2.5px solid #a855f7;
            }
            QSpinBox:hover, QDoubleSpinBox:hover {
                border: 2px solid #a855f7;
            }
            QSpinBox:focus, QDoubleSpinBox:focus {
                border: 2.5px solid #a855f7;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                width: 0;
                height: 0;
                border: none;
                background: none;
            }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                image: none;
            }

            /* === Check Boxes — Pill Toggles === */
            QCheckBox {
                color: #c0caf5;
                font-size: 12px;
                font-weight: 500;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 36px;
                height: 20px;
                border-radius: 10px;
                border: none;
                background: rgba(86, 95, 137, 0.3);
            }
            QCheckBox::indicator:hover {
                background: rgba(86, 95, 137, 0.45);
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6486ff,
                    stop:1 #a855f7);
            }

            /* === Push Buttons — Holographic Pills === */
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(47,51,72,0.85),
                    stop:1 rgba(41,46,66,0.85));
                color: #c0caf5;
                border: 1.5px solid rgba(60, 65, 90, 0.4);
                border-radius: 10px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(100,134,255,0.2),
                    stop:1 rgba(168,85,247,0.2));
                border-color: rgba(122, 162, 247, 0.5);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(100,134,255,0.3),
                    stop:1 rgba(168,85,247,0.3));
            }
            QPushButton:disabled {
                background: rgba(30, 32, 48, 0.5);
                color: rgba(86, 95, 137, 0.6);
                border-color: rgba(60, 65, 90, 0.2);
            }

            /* === Sliders — Liquid Track === */
            QSlider::groove:horizontal {
                background: rgba(142, 142, 147, 0.2);
                height: 8px;
                border-radius: 4px;
                border: none;
            }
            QSlider::handle:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff,
                    stop:1 #f0f0f2);
                border: 2px solid #6486ff;
                width: 20px;
                height: 20px;
                border-radius: 10px;
                margin: -6px 0;
            }
            QSlider::handle:horizontal:hover {
                background: #ffffff;
                border-color: #a855f7;
                border-width: 2.5px;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6486ff,
                    stop:1 #a855f7);
                border-radius: 4px;
            }

            /* === Status Bar === */
            QStatusBar {
                color: #565f89;
                background: transparent;
                border-top: 1px solid rgba(60, 65, 90, 0.3);
                font-size: 12px;
                font-weight: 500;
            }

            /* === Scroll Area === */
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background-color: rgba(142, 142, 147, 0.1);
                width: 10px;
                border-radius: 5px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: rgba(142, 142, 147, 0.35);
                border-radius: 5px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(142, 142, 147, 0.55);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }

            /* === Text Edit (debug log) === */
            QTextEdit {
                background: rgba(17, 17, 27, 0.9);
                color: #a9b1d6;
                border: 1.5px solid rgba(60, 65, 90, 0.4);
                border-radius: 12px;
                padding: 6px;
                selection-background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6486ff,
                    stop:1 #a855f7);
            }
            QTextEdit::selection {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6486ff,
                    stop:1 #a855f7);
            }
        """
    
    def keyPressEvent(self, event):
        """Handle keyboard shortcuts.
        
        - Space: Play/Pause toggle
        - Escape: Stop playback and release all notes
        """
        from PyQt6.QtCore import Qt
        key = event.key()
        
        if key == Qt.Key.Key_Space:
            if self._engine.is_playing():
                self._pause_playback()
            elif self._engine.video_source.is_loaded:
                self._start_playback()
            event.accept()
        elif key == Qt.Key.Key_Escape:
            if self._engine.is_running or self._engine.video_source.is_playing:
                self._stop_playback()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """Save settings and cleanup on window close."""
        # Persist settings before exiting
        self._settings.save_to_file()
        self._engine.cleanup()
        event.accept()


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())