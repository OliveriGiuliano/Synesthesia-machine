"""
Main application window for the Synesthesia Machine.
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
    QSlider
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap, QFont

from synesthesia_machine.core.video_source import VideoSource
from synesthesia_machine.core.midi_output import MidiOutput
from synesthesia_machine.core.audio_engine import AudioEngine
from synesthesia_machine.synesthesia_modes.base_mode import SynesthesiaMode, NoteEvent
from synesthesia_machine.synesthesia_modes.color_to_note import ColorToNoteMode
from synesthesia_machine.gui.visualizer import NoteVisualizer
from synesthesia_machine.utils.musical_scales import (
    get_scale_notes, get_available_scales, get_note_name
)
from synesthesia_machine.utils.color_analysis import downscale_frame
from synesthesia_machine.config.settings import AppSettings


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self._settings = AppSettings()
        self._video_source = VideoSource()
        self._midi_output = MidiOutput()
        self._audio_engine = AudioEngine()
        self._current_mode: Optional[SynesthesiaMode] = None
        self._available_modes: Dict[str, type] = {}
        self._current_frame: Optional[np.ndarray] = None
        self._processing_timer = QTimer()
        self._processing_timer.setInterval(16)  # ~60fps processing cap
        self._processing_timer.timeout.connect(self._process_frame)
        self._is_running = False
        
        # Performance tracking
        self._debug_enabled = False
        self._fps_history = deque(maxlen=60)  # Last 60 frame timestamps
        self._frame_times = deque(maxlen=60)  # Frame processing times in ms
        self._last_frame_time = 0.0
        self._current_fps = 0.0
        self._avg_fps = 0.0
        self._avg_frame_time_ms = 0.0
        self._max_frame_time_ms = 0.0          # Longest frame processing time
        self._slow_frame_count = 0             # Frames exceeding 60fps threshold
        self._total_frame_count = 0            # Total frames processed
        self._FRAME_TIME_TARGET_MS = 16.67     # Target: 60fps = 1000/60 ms per frame
        
        # Register available synesthesia modes
        self._register_modes()
        self._init_ui()
        self._connect_signals()
        self._initialize_mode()
        self._update_scale_display()
    
    def _register_modes(self):
        """Register all available synesthesia modes."""
        self._available_modes = {
            "color_to_note": ColorToNoteMode,
        }
    
    def _init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("Synesthesia Machine")
        self.setMinimumSize(1200, 800)
        self.setStyleSheet(self._get_stylesheet())
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Left panel - Video + Visualizer
        left_panel = self._create_left_panel()
        main_layout.addWidget(left_panel, stretch=3)
        
        # Right panel - Controls
        right_panel = self._create_right_panel()
        main_layout.addWidget(right_panel, stretch=1)
        
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
            # Get all text, keep only last 50 lines
            text = self._debug_log.toPlainText()
            lines = text.split('\n')
            lines = lines[-50:]
            self._debug_log.setPlainText('\n'.join(lines))
            # Scroll to bottom
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
        
        # Track slow frames (exceeding 60fps target of 16.67ms)
        if frame_time_ms > self._FRAME_TIME_TARGET_MS:
            self._slow_frame_count += 1
        
        # Track maximum frame time
        if frame_time_ms > self._max_frame_time_ms:
            self._max_frame_time_ms = frame_time_ms
        
        # Calculate current FPS from recent history
        if len(self._fps_history) > 1:
            time_span = self._fps_history[-1] - self._fps_history[0]
            if time_span > 0:
                self._current_fps = len(self._fps_history) / time_span
        
        # Calculate average FPS from history
        if self._frame_times:
            self._avg_frame_time_ms = sum(self._frame_times) / len(self._frame_times)
            self._avg_fps = 1000.0 / self._avg_frame_time_ms if self._avg_frame_time_ms > 0 else 0
        
        # Calculate slow frame percentage
        slow_pct = (self._slow_frame_count / self._total_frame_count * 100) if self._total_frame_count > 0 else 0.0
        
        # Update labels
        self._fps_label.setText(f"FPS: {self._current_fps:.1f}")
        self._frame_time_label.setText(f"Frame: {frame_time_ms:.1f} ms")
        self._avg_fps_label.setText(f"Avg: {self._avg_fps:.1f} fps")
        self._max_frame_label.setText(f"Max: {self._max_frame_time_ms:.1f} ms")
        self._slow_frame_label.setText(f"Slow: {self._slow_frame_count} ({slow_pct:.1f}%)")
        
        # Color code FPS label based on current performance
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
        layout.setSpacing(10)
        
        # Debug panel (collapsible)
        debug_group = QGroupBox("Debug Panel")
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
        video_group = QGroupBox("Video Input")
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
        
        # Buttons use global stylesheet
        for btn in [self._btn_load_file, self._btn_camera,
                     self._btn_play, self._btn_pause, self._btn_stop]:
            btn.setMaximumHeight(32)
        
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
        # Seek slider uses global stylesheet
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
        viz_group = QGroupBox("Note Visualizer")
        viz_layout = QVBoxLayout()
        
        self._visualizer = NoteVisualizer()
        viz_layout.addWidget(self._visualizer)
        viz_group.setLayout(viz_layout)
        layout.addWidget(viz_group, stretch=1)
        
        return panel
    
    def _create_right_panel(self) -> QWidget:
        """Create the right panel with all controls."""
        panel = QWidget()
        panel.setMaximumWidth(350)
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        
        # Scroll area for controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(12)
        
        # Synesthesia Mode Selection
        mode_group = QGroupBox("Synesthesia Mode")
        mode_layout = QVBoxLayout()
        
        self._mode_combo = QComboBox()
        for mode_id in self._available_modes:
            mode_class = self._available_modes[mode_id]
            temp = mode_class()
            self._mode_combo.addItem(temp.get_name(), mode_id)
            temp.deleteLater()
        mode_layout.addWidget(self._mode_combo)
        
        # Mode description label
        self._mode_desc_label = QLabel()
        self._mode_desc_label.setStyleSheet("color: #999; font-size: 11px;")
        self._mode_desc_label.setWordWrap(True)
        mode_layout.addWidget(self._mode_desc_label)
        
        # Mode parameters (populated when mode changes)
        self._mode_params_widget = QWidget()
        self._mode_params_layout = QVBoxLayout(self._mode_params_widget)
        mode_layout.addWidget(self._mode_params_widget)
        
        mode_group.setLayout(mode_layout)
        scroll_layout.addWidget(mode_group)
        
        # General Controls
        general_group = QGroupBox("General Controls")
        general_layout = QVBoxLayout()
        
        # Volume
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
        
        # Mute
        self._mute_checkbox = QCheckBox("Mute")
        general_layout.addWidget(self._mute_checkbox)
        
        general_group.setLayout(general_layout)
        scroll_layout.addWidget(general_group)
        
        # Musical Scale
        scale_group = QGroupBox("Musical Scale")
        scale_layout = QVBoxLayout()
        
        # Scale type
        scale_type_layout = QHBoxLayout()
        scale_type_layout.addWidget(QLabel("Scale:"))
        self._scale_combo = QComboBox()
        for scale_name in get_available_scales():
            display = scale_name.replace("_", " ").title()
            self._scale_combo.addItem(display, scale_name)
        scale_type_layout.addWidget(self._scale_combo)
        scale_layout.addLayout(scale_type_layout)
        
        # Root note
        root_layout = QHBoxLayout()
        root_layout.addWidget(QLabel("Root Note:"))
        self._root_note_spin = QSpinBox()
        self._root_note_spin.setMinimum(0)
        self._root_note_spin.setMaximum(127)
        self._root_note_spin.setValue(60)  # C5
        self._root_note_spin.setSuffix(" (MIDI)")
        root_layout.addWidget(self._root_note_spin)
        scale_layout.addLayout(root_layout)
        
        # Note range
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("Note Range:"))
        self._min_note_spin = QSpinBox()
        self._min_note_spin.setMinimum(0)
        self._min_note_spin.setMaximum(127)
        self._min_note_spin.setValue(24)  # C1
        self._min_note_spin.setSuffix(" Min")
        self._max_note_spin = QSpinBox()
        self._max_note_spin.setMinimum(0)
        self._max_note_spin.setMaximum(127)
        self._max_note_spin.setValue(96)  # C8
        self._max_note_spin.setSuffix(" Max")
        range_layout.addWidget(self._min_note_spin)
        range_layout.addWidget(self._max_note_spin)
        scale_layout.addLayout(range_layout)
        
        # Scale preview
        self._scale_preview_label = QLabel()
        self._scale_preview_label.setStyleSheet("color: #aaa; font-family: monospace; font-size: 12px;")
        self._scale_preview_label.setWordWrap(True)
        scale_layout.addWidget(self._scale_preview_label)
        
        scale_group.setLayout(scale_layout)
        scroll_layout.addWidget(scale_group)
        
        # MIDI Output
        midi_group = QGroupBox("MIDI Output")
        midi_layout = QVBoxLayout()
        
        # Output type
        self._midi_type_combo = QComboBox()
        self._midi_type_combo.addItem("Internal (PyGame)", "internal")
        midi_layout.addWidget(self._midi_type_combo)
        
        # Refresh button
        self._btn_refresh_midi = QPushButton("🔄 Refresh Devices")
        self._btn_refresh_midi.setMaximumHeight(28)
        midi_layout.addWidget(self._btn_refresh_midi)
        
        midi_group.setLayout(midi_layout)
        scroll_layout.addWidget(midi_group)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        return panel
    
    def _connect_signals(self):
        """Connect all UI signals."""
        # Video controls
        self._btn_load_file.clicked.connect(self._load_video_file)
        self._btn_camera.clicked.connect(self._connect_camera)
        self._btn_play.clicked.connect(self._start_playback)
        self._btn_pause.clicked.connect(self._pause_playback)
        self._btn_stop.clicked.connect(self._stop_playback)
        self._seek_slider.sliderReleased.connect(self._seek_released)
        
        # Video source signals
        self._video_source.frame_ready.connect(self._on_frame_ready)
        self._video_source.position_changed.connect(self._on_position_changed)
        self._video_source.playback_ended.connect(self._on_playback_ended)
        self._video_source.error_occurred.connect(self._on_video_error)
        
        # MIDI signals
        self._midi_output.devices_updated.connect(self._on_midi_devices_updated)
        self._midi_output.error_occurred.connect(self._on_midi_error)
        
        # Audio engine signals
        self._audio_engine.notes_changed.connect(self._on_audio_notes_changed)
        
        # Mode selection
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        
        # Scale
        self._scale_combo.currentIndexChanged.connect(self._update_scale_display)
        self._root_note_spin.valueChanged.connect(self._update_scale_display)
        self._min_note_spin.valueChanged.connect(self._update_scale_display)
        self._max_note_spin.valueChanged.connect(self._update_scale_display)
        
        # Audio
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._mute_checkbox.toggled.connect(self._on_mute_toggled)
        
        # MIDI refresh
        self._btn_refresh_midi.clicked.connect(self._refresh_midi_devices)
    
    def _initialize_mode(self):
        """Initialize the first synesthesia mode."""
        if self._available_modes:
            first_id = list(self._available_modes.keys())[0]
            self._current_mode = self._available_modes[first_id]()
            self._populate_mode_parameters()
            self._update_mode_description()
    
    def _populate_mode_parameters(self):
        """Populate the mode-specific parameters UI."""
        # Clear existing
        while self._mode_params_layout.count():
            child = self._mode_params_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        if self._current_mode is None:
            return
        
        params = self._current_mode.get_parameters()
        for param_name, param_value in params.items():
            label = QLabel(param_name.replace("_", " ").title())
            label.setStyleSheet("color: #bbb; font-size: 11px;")
            self._mode_params_layout.addWidget(label)
            
            if param_name == "activation_threshold":
                # Percentage 0-100, float
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
                self._mode_params_layout.addWidget(spinner)
            elif isinstance(param_value, int):
                spinner = QSpinBox()
                spinner.setMinimum(0)
                spinner.setMaximum(10000)
                spinner.setValue(param_value)
                spinner.setObjectName(param_name)
                spinner.valueChanged.connect(
                    lambda v, n=param_name: self._on_mode_param_changed(n, v)
                )
                self._mode_params_layout.addWidget(spinner)
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
                self._mode_params_layout.addWidget(spinner)
    
    def _update_mode_description(self):
        """Update the mode description label."""
        if self._current_mode:
            self._mode_desc_label.setText(self._current_mode.get_description())
    
    def _update_scale_display(self):
        """Update the scale preview and apply to visualizer."""
        scale_type = self._scale_combo.currentData()
        root_note = self._root_note_spin.value()
        min_note = self._min_note_spin.value()
        max_note = self._max_note_spin.value()
        
        notes = get_scale_notes(scale_type, root_note, min_note=min_note, max_note=max_note)
        note_names = [get_note_name(n) for n in notes]
        self._scale_preview_label.setText("Notes: " + ", ".join(note_names))
        
        # Update visualizer
        self._visualizer.set_scale_notes(notes)
        
        # Force-reprocess current frame so notes outside new range are released
        # This fixes the bug where changing scale params while paused leaves notes stuck
        if self._current_frame is not None and self._current_mode is not None:
            self._force_process_frame()
    
    def _force_process_frame(self):
        """Process current frame immediately (used when parameters change)."""
        if self._current_frame is None or self._current_mode is None:
            return
        
        try:
            from synesthesia_machine.utils.color_analysis import downscale_frame
            proc_w = self._settings.video.processing_width
            proc_h = self._settings.video.processing_height
            small_frame = downscale_frame(self._current_frame, proc_w, proc_h)
            
            scale_type = self._scale_combo.currentData()
            root_note = self._root_note_spin.value()
            min_note = self._min_note_spin.value()
            max_note = self._max_note_spin.value()
            scale_notes = get_scale_notes(scale_type, root_note, min_note=min_note, max_note=max_note)
            
            # Clear active notes first to ensure a clean slate
            # This prevents stuck notes when the same note gets a different MIDI number
            # or when scale changes cause note reassignment
            for event in self._current_mode.clear_active_notes():
                self._send_event(event)
            
            events = self._current_mode.process_frame(small_frame, scale_notes)
            for event in events:
                self._send_event(event)
        except Exception as e:
            self._status_bar.showMessage(f"Force process error: {e}")
    
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
            if self._video_source.open_file(file_path):
                self._settings.video.file_path = file_path
                self._settings.video.source_type = "file"
                self._status_bar.showMessage(f"Loaded: {os.path.basename(file_path)}")
                self._seek_slider.setEnabled(True)
                # Show first frame
                self._video_source.start()
                self._is_running = True
                self._processing_timer.start()
            else:
                QMessageBox.warning(self, "Error", "Failed to open video file.")
    
    def _connect_camera(self):
        """Connect to a camera device."""
        indices = [0, 1, 2]
        for idx in indices:
            if self._video_source.open_camera(idx):
                self._settings.video.source_type = "camera"
                self._settings.video.camera_index = idx
                self._status_bar.showMessage(f"Camera {idx} connected")
                self._seek_slider.setEnabled(False)
                self._video_source.start()
                self._is_running = True
                self._processing_timer.start()
                return
        QMessageBox.warning(self, "Error", "No camera device found.")
    
    def _start_playback(self):
        if not self._video_source.is_playing:
            if self._is_running:
                self._video_source.resume()
                self._status_bar.showMessage("Playing")
            else:
                self._status_bar.showMessage("No video source loaded")
    
    def _pause_playback(self):
        self._video_source.pause()
        self._status_bar.showMessage("Paused")
    
    def _stop_playback(self):
        self._video_source.stop()
        self._is_running = False
        self._processing_timer.stop()
        if self._current_mode:
            for event in self._current_mode.clear_active_notes():
                self._send_event(event)
        self._audio_engine.stop_all()
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
        if self._video_source.source_type == "file":
            total = self._video_source.get_total_frames()
            if total > 0:
                value = self._seek_slider.value()
                frame = value
                self._video_source.seek_to_frame(frame)
    
    def _on_frame_ready(self, frame_rgb: np.ndarray):
        """Called when a new frame is available from the video source."""
        try:
            # Make a copy to ensure we own the data
            self._current_frame = frame_rgb.copy()
            
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
            
            fps = self._video_source.get_fps()
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
    
    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"
    
    # --- Frame processing ---
    
    def _process_frame(self):
        """Process the current frame through the synesthesia mode."""
        if self._current_frame is None or self._current_mode is None:
            return
        
        if not self._video_source.is_playing:
            return
        
        frame_start = time.perf_counter()
        
        try:
            # Downscale for processing
            proc_w = self._settings.video.processing_width
            proc_h = self._settings.video.processing_height
            small_frame = downscale_frame(self._current_frame, proc_w, proc_h)
            
            # Get current scale notes
            scale_type = self._scale_combo.currentData()
            root_note = self._root_note_spin.value()
            min_note = self._min_note_spin.value()
            max_note = self._max_note_spin.value()
            scale_notes = get_scale_notes(scale_type, root_note, min_note=min_note, max_note=max_note)
            
            # Process through current mode
            events = self._current_mode.process_frame(small_frame, scale_notes)
            
            # Send events
            for event in events:
                self._send_event(event)
        except Exception as e:
            self._status_bar.showMessage(f"Processing error: {e}")
        
        # Track performance
        frame_time = (time.perf_counter() - frame_start) * 1000.0  # ms
        self._update_performance_display(frame_time)
    
    def _send_event(self, event: NoteEvent):
        """Send a note event to MIDI output and/or audio engine."""
        midi_type = self._midi_type_combo.currentData()
        
        # Debug output
        state = "ON" if event.is_on else "OFF"
        vel_str = f" vel={event.velocity}" if event.is_on else ""
        self._debug_print(f"NOTE {state} ch=1 note={event.note} ({self._get_note_name(event.note)}){vel_str} | active: {sorted(self._audio_engine.active_notes)}")
        
        if event.is_on:
            self._midi_output.send_note_on(event.note, event.velocity)
            if midi_type == "internal":
                self._audio_engine.play_note(event.note, event.velocity)
        else:
            self._midi_output.send_note_off(event.note)
            if midi_type == "internal":
                self._audio_engine.stop_note(event.note)
        
        # Update visualizer from mode's active notes
        if self._current_mode:
            self._visualizer.update_active_notes(self._current_mode.active_notes)
    
    def _get_note_name(self, midi_note: int) -> str:
        """Get note name for a MIDI note number."""
        notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        octave = (midi_note // 12) - 1
        name = notes[midi_note % 12]
        return f"{name}{octave}"
    
    # --- Mode handling ---
    
    def _on_mode_changed(self, index: int):
        """Handle synesthesia mode selection change."""
        mode_id = self._mode_combo.itemData(index)
        
        # Clear previous mode notes
        if self._current_mode:
            for event in self._current_mode.clear_active_notes():
                self._send_event(event)
        
        # Create new mode instance
        mode_class = self._available_modes.get(mode_id)
        if mode_class:
            self._current_mode = mode_class()
            self._populate_mode_parameters()
            self._update_mode_description()
            self._status_bar.showMessage(f"Mode: {self._current_mode.get_name()}")
    
    def _on_mode_param_changed(self, param_name: str, value):
        """Handle mode parameter change."""
        if self._current_mode:
            self._current_mode.update_parameters({param_name: value})
        # Force-reprocess so parameter changes take effect immediately
        # (e.g., raising threshold releases notes even when paused)
        self._force_process_frame()
    
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
    
    def _on_audio_notes_changed(self, active_notes):
        """Called when audio engine active notes change (note ended naturally)."""
        notes_list = sorted(active_notes) if active_notes else []
        self._debug_print(f"AUDIO active notes changed: {notes_list}")
    
    def _refresh_midi_devices(self):
        self._midi_output.refresh_devices()
    
    # --- Audio handling ---
    
    def _on_volume_changed(self, value: int):
        volume = value / 100.0
        self._volume_label.setText(f"{value}%")
        self._audio_engine.set_volume(volume)
    
    def _on_mute_toggled(self, muted: bool):
        self._audio_engine.set_muted(muted)
    
    # --- Styling ---
    
    def _get_stylesheet(self) -> str:
        return """
            /* === Window & Background === */
            QMainWindow {
                background-color: #1a1b26;
            }

            /* === Group Boxes === */
            QGroupBox {
                background-color: #1e2030;
                border: 1px solid #2f3348;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                font-size: 13px;
                font-weight: bold;
                color: #c0caf5;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px 4px 8px;
                color: #7aa2f7;
                font-size: 12px;
                font-weight: bold;
                text-transform: uppercase;
                letter-spacing: 1.0px;
            }

            /* === Labels === */
            QLabel {
                color: #565f89;
                font-size: 11px;
            }

            /* === Combo Boxes (dropdowns) === */
            QComboBox {
                background-color: #15161e;
                color: #c0caf5;
                border: 1px solid #2f3348;
                border-radius: 6px;
                padding: 6px 12px;
                min-height: 24px;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #7aa2f7;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
                outline: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #7aa2f7;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e2030;
                border: 1px solid #2f3348;
                border-radius: 6px;
                color: #c0caf5;
                selection-background-color: #3b4261;
                selection-color: #c0caf5;
                outline: none;
                padding: 4px;
                gridline-color: #2f3348;
            }
            QComboBox QAbstractItemView::item {
                min-height: 28px;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #3b4261;
                color: #c0caf5;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #292e42;
                color: #c0caf5;
            }

            /* === Spin Boxes === */
            QSpinBox, QDoubleSpinBox {
                background-color: #15161e;
                color: #c0caf5;
                border: 1px solid #2f3348;
                border-radius: 6px;
                padding: 5px 8px;
                min-height: 24px;
                font-size: 12px;
            }
            QSpinBox:hover, QDoubleSpinBox:hover {
                border-color: #7aa2f7;
            }
            QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #7aa2f7;
                border-width: 2px;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                background-color: #1e2030;
                border: none;
                width: 18px;
            }
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #292e42;
            }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid #7aa2f7;
                margin-bottom: 2px;
            }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #7aa2f7;
                margin-top: 2px;
            }

            /* === Check Boxes === */
            QCheckBox {
                color: #a9b1d6;
                font-size: 12px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #2f3348;
                background-color: #15161e;
            }
            QCheckBox::indicator:hover {
                border-color: #7aa2f7;
            }
            QCheckBox::indicator:checked {
                background-color: #7aa2f7;
                border-color: #7aa2f7;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iOSIgdmlld0JveD0iMCAwIDEyIDkiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHBhdGggZD0iTTEgNEw0LjUgNy41TDExIDEiIHN0cm9rZT0iIzFhMWIyNiIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4=);
            }

            /* === Push Buttons === */
            QPushButton {
                background-color: #2f3348;
                color: #c0caf5;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3b4261;
                border-color: #7aa2f7;
            }
            QPushButton:pressed {
                background-color: #292e42;
            }
            QPushButton:disabled {
                background-color: #1e2030;
                color: #565f89;
            }

            /* === Sliders === */
            QSlider::groove:horizontal {
                background: #15161e;
                height: 6px;
                border-radius: 3px;
                border: 1px solid #2f3348;
            }
            QSlider::handle:horizontal {
                background: #7aa2f7;
                border: 2px solid #7aa2f7;
                width: 16px;
                height: 16px;
                border-radius: 8px;
                margin: -8px 0;
            }
            QSlider::handle:horizontal:hover {
                background: #89b4fa;
                border-color: #89b4fa;
            }
            QSlider::sub-page:horizontal {
                background: #7aa2f7;
                border-radius: 3px;
            }

            /* === Status Bar === */
            QStatusBar {
                color: #565f89;
                background-color: #1a1b26;
                border-top: 1px solid #2f3348;
                font-size: 11px;
            }

            /* === Scroll Area === */
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background-color: #1a1b26;
                width: 8px;
                border-radius: 4px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #2f3348;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #3b4261;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }

            /* === Text Edit (debug log) === */
            QTextEdit {
                background-color: #15161e;
                color: #a9b1d6;
                border: 1px solid #2f3348;
                border-radius: 4px;
                padding: 4px;
                selection-background-color: #3b4261;
            }
            QTextEdit::selection {
                background-color: #3b4261;
            }
        """
    
    def closeEvent(self, event):
        """Cleanup on window close."""
        self._stop_playback()
        self._video_source.stop()
        self._midi_output.close()
        self._audio_engine.cleanup()
        event.accept()


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())