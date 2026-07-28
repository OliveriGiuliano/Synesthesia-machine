"""
Synesthesia Engine - Central controller for the synesthesia processing pipeline.

Orchestrates the flow: video_frame -> downscale -> mode.process -> events -> MIDI/audio.

This class owns VideoSource, MidiOutput, AudioEngine, and the active SynesthesiaMode,
freeing MainWindow to focus purely on UI presentation.

Signals:
    active_notes_changed: Emitted when the set of active notes changes (for visualizer)
    frame_processed: Emitted after each frame is processed (for performance tracking)
    error_occurred: Emitted when an error occurs during processing
"""

import time
import logging
import numpy as np
from typing import Dict, List, Optional, Type
from collections import deque
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from synesthesia_machine.core.video_source import VideoSource
from synesthesia_machine.core.midi_output import MidiOutput
from synesthesia_machine.core.audio_engine import AudioEngine
from synesthesia_machine.synesthesia_modes.base_mode import SynesthesiaMode, NoteEvent
from synesthesia_machine.utils.musical_scales import get_scale_notes
from synesthesia_machine.utils.color_analysis import downscale_frame
from synesthesia_machine.config.settings import AppSettings

logger = logging.getLogger(__name__)


class SynesthesiaEngine(QObject):
    """
    Central controller for the synesthesia processing pipeline.
    
    Owns the core components (video, MIDI, audio, mode) and coordinates
    the frame processing loop. MainWindow connects to this engine's signals
    to update the UI.
    
    Signals:
        active_notes_changed: Dict[int, int] - current active notes for visualizer
        frame_processed: float - processing time in ms for performance tracking
        error_occurred: str - error message
        mode_ready: emitted when a new mode is initialized
    """
    
    active_notes_changed = pyqtSignal(object)  # Dict[int, int]
    frame_processed = pyqtSignal(float)  # processing time in ms
    error_occurred = pyqtSignal(str)  # error message
    mode_ready = pyqtSignal()  # emitted when mode is ready
    
    def __init__(self, parent=None, settings: Optional[AppSettings] = None):
        super().__init__(parent)
        
        # Core components
        self._video_source = VideoSource()
        self._midi_output = MidiOutput()
        self._audio_engine = AudioEngine()
        
        # Synesthesia mode
        self._current_mode: Optional[SynesthesiaMode] = None
        self._available_modes: Dict[str, Type[SynesthesiaMode]] = {}
        
        # Settings (use provided instance or create defaults)
        self._settings = settings or AppSettings()
        # Validate settings immediately to catch corrupted/out-of-range values
        self._settings.validate()

        # Processing state
        self._current_frame: Optional[np.ndarray] = None
        self._processing_timer = QTimer()
        self._processing_timer.setInterval(16)  # ~60fps cap
        self._processing_timer.timeout.connect(self._process_frame)
        self._is_running = False
        
        # Scale state
        self._scale_type = "chromatic"
        self._root_note = 60
        self._min_note = 24
        self._max_note = 96
        
        # MIDI output type ("internal" or "device:N")
        self._midi_output_type = "internal"
        
        # Performance tracking
        self._fps_history = deque(maxlen=60)
        self._frame_times = deque(maxlen=60)
        self._current_fps = 0.0
        self._avg_fps = 0.0
        self._avg_frame_time_ms = 0.0
        self._max_frame_time_ms = 0.0
        self._slow_frame_count = 0
        self._total_frame_count = 0
        self._FRAME_TIME_TARGET_MS = 16.67
        
        # Connect video source signals for internal tracking
        self._video_source.frame_ready.connect(self._on_frame_ready)
        
        # Internal frame storage signal
        self._internal_frame_ready = None
    
    # --- Properties ---
    
    @property
    def video_source(self) -> VideoSource:
        """Access the video source component."""
        return self._video_source
    
    @property
    def midi_output(self) -> MidiOutput:
        """Access the MIDI output component."""
        return self._midi_output
    
    @property
    def audio_engine(self) -> AudioEngine:
        """Access the audio engine component."""
        return self._audio_engine
    
    @property
    def current_mode(self) -> Optional[SynesthesiaMode]:
        """Access the current synesthesia mode."""
        return self._current_mode
    
    @property
    def settings(self) -> AppSettings:
        """Access the application settings."""
        return self._settings
    
    @property
    def is_running(self) -> bool:
        """Whether the engine is actively processing."""
        return self._is_running
    
    @property
    def current_fps(self) -> float:
        """Current frames per second."""
        return self._current_fps
    
    @property
    def avg_fps(self) -> float:
        """Average frames per second."""
        return self._avg_fps
    
    @property
    def avg_frame_time_ms(self) -> float:
        """Average frame processing time in milliseconds."""
        return self._avg_frame_time_ms
    
    @property
    def max_frame_time_ms(self) -> float:
        """Maximum frame processing time in milliseconds."""
        return self._max_frame_time_ms
    
    @property
    def slow_frame_count(self) -> int:
        """Number of frames that exceeded the target frame time."""
        return self._slow_frame_count
    
    @property
    def total_frame_count(self) -> int:
        """Total number of frames processed."""
        return self._total_frame_count
    
    # --- Mode Registration ---
    
    def register_mode(self, mode_id: str, mode_class: Type[SynesthesiaMode]):
        """Register a synesthesia mode."""
        self._available_modes[mode_id] = mode_class
    
    def get_available_modes(self) -> Dict[str, Type[SynesthesiaMode]]:
        """Return dict of registered modes {id: class}."""
        return self._available_modes.copy()
    
    # --- Mode Management ---
    
    def initialize_mode(self, mode_id: str):
        """Initialize a synesthesia mode by ID."""
        mode_class = self._available_modes.get(mode_id)
        if not mode_class:
            self.error_occurred.emit(f"Unknown mode: {mode_id}")
            return
        
        # Clear previous mode's notes
        if self._current_mode:
            for event in self._current_mode.clear_active_notes():
                self._send_event(event)
        
        self._current_mode = mode_class()
        self._current_mode.events_ready.connect(self._on_mode_events)
        self.mode_ready.emit()
    
    def switch_mode(self, mode_id: str):
        """Switch to a different synesthesia mode."""
        self.initialize_mode(mode_id)
    
    def update_mode_parameters(self, params: Dict[str, any]):
        """Update mode-specific parameters and reprocess."""
        if self._current_mode:
            self._current_mode.update_parameters(params)
            self._force_process_frame()
    
    def update_mode_parameter(self, name: str, value):
        """Update a single mode parameter and reprocess."""
        self.update_mode_parameters({name: value})
    
    # --- Scale Management ---
    
    def set_scale(self, scale_type: str, root_note: int = 60,
                  min_note: int = 24, max_note: int = 96):
        """Set the musical scale parameters."""
        self._scale_type = scale_type
        self._root_note = root_note
        self._min_note = min_note
        self._max_note = max_note
        # Reprocess current frame with new scale
        self._force_process_frame()
    
    def get_scale_notes(self) -> List[int]:
        """Get the current scale notes."""
        return get_scale_notes(
            self._scale_type, self._root_note,
            min_note=self._min_note, max_note=self._max_note
        )
    
    def get_current_scale_type(self) -> str:
        """Get the current scale type."""
        return self._scale_type
    
    # --- Audio Control ---
    
    def set_volume(self, volume: float):
        """Set audio volume (0.0 to 1.0)."""
        self._audio_engine.set_volume(volume)
    
    def set_muted(self, muted: bool):
        """Set audio mute state."""
        self._audio_engine.set_muted(muted)
    
    # --- MIDI Output ---
    
    def set_midi_output_type(self, output_type: str):
        """Set MIDI output type ('internal' or 'device:N')."""
        self._midi_output_type = output_type
    
    def refresh_midi_devices(self):
        """Refresh the list of available MIDI devices."""
        self._midi_output.refresh_devices()
    
    # --- Playback Control ---
    
    def start_processing(self):
        """Start the processing timer."""
        self._is_running = True
        self._processing_timer.start()
    
    def stop_processing(self):
        """Stop processing and release all notes."""
        self._is_running = False
        self._processing_timer.stop()
        if self._current_mode:
            for event in self._current_mode.clear_active_notes():
                self._send_event(event)
        self._audio_engine.stop_all()
    
    def is_playing(self) -> bool:
        """Check if video source is playing."""
        return self._video_source.is_playing
    
    # --- Frame Processing Pipeline ---
    
    def _on_frame_ready(self, frame_rgb: np.ndarray):
        """Called when a new frame is available. Store for processing."""
        self._current_frame = frame_rgb.copy()
        # Emit the frame for UI display
        if hasattr(self, '_frame_for_display'):
            self._frame_for_display.emit(frame_rgb)
    
    def _process_frame(self):
        """
        Core processing pipeline:
        1. Check video is playing
        2. Downscale frame
        3. Get scale notes
        4. Process through synesthesia mode
        5. Send events to MIDI/audio
        """
        if self._current_frame is None or self._current_mode is None:
            return
        
        if not self._video_source.is_playing:
            return
        
        frame_start = time.perf_counter()
        
        try:
            # Downscale for processing
            proc_w = self._settings.video.processing_width
            proc_h = self._settings.video.processing_height
            small_frame = downscale_frame(
                self._current_frame, proc_w, proc_h
            )
            
            # Get current scale notes
            scale_notes = self.get_scale_notes()
            
            # Process through current mode
            events = self._current_mode.process_frame(small_frame, scale_notes)
            
            # Send events to outputs
            for event in events:
                self._send_event(event)
                
        except Exception as e:
            logger.error(f"Processing error: {e}", exc_info=True)
            self.error_occurred.emit(f"Processing error: {e}")
        
        # Track performance
        frame_time = (time.perf_counter() - frame_start) * 1000.0
        self._update_performance(frame_time)
        self.frame_processed.emit(frame_time)
    
    def _force_process_frame(self):
        """
        Process the current frame immediately, bypassing the timer.
        Used when parameters change to update note state right away.
        """
        if self._current_frame is None or self._current_mode is None:
            return
        
        try:
            proc_w = self._settings.video.processing_width
            proc_h = self._settings.video.processing_height
            small_frame = downscale_frame(
                self._current_frame, proc_w, proc_h
            )
            
            scale_notes = self.get_scale_notes()
            
            # Clear active notes for a clean slate
            for event in self._current_mode.clear_active_notes():
                self._send_event(event)
            
            events = self._current_mode.process_frame(small_frame, scale_notes)
            for event in events:
                self._send_event(event)
                
        except Exception as e:
            logger.error(f"Force process error: {e}", exc_info=True)
            self.error_occurred.emit(f"Force process error: {e}")
    
    def _on_mode_events(self, events: List[NoteEvent]):
        """Handle events emitted by the synesthesia mode."""
        # Events are already sent via _send_event in process_frame
        # This handler is for modes that emit events asynchronously
        for event in events:
            self._send_event(event)
    
    def _send_event(self, event: NoteEvent):
        """Route a note event to MIDI output and/or audio engine."""
        if event.is_on:
            self._midi_output.send_note_on(event.note, event.velocity)
            if self._midi_output_type == "internal":
                self._audio_engine.play_note(event.note, event.velocity)
        else:
            self._midi_output.send_note_off(event.note)
            if self._midi_output_type == "internal":
                self._audio_engine.stop_note(event.note)
        
        # Update visualizer with current active notes
        if self._current_mode:
            self.active_notes_changed.emit(self._current_mode.active_notes)
    
    # --- Performance Tracking ---
    
    def _update_performance(self, frame_time_ms: float):
        """Update performance metrics."""
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
    
    def get_performance_summary(self) -> Dict[str, any]:
        """Get a summary of performance metrics."""
        slow_pct = (
            (self._slow_frame_count / self._total_frame_count * 100)
            if self._total_frame_count > 0 else 0.0
        )
        return {
            'current_fps': self._current_fps,
            'avg_fps': self._avg_fps,
            'avg_frame_time_ms': self._avg_frame_time_ms,
            'max_frame_time_ms': self._max_frame_time_ms,
            'slow_frame_count': self._slow_frame_count,
            'slow_frame_pct': slow_pct,
            'total_frame_count': self._total_frame_count,
        }
    
    # --- Cleanup ---
    
    def cleanup(self):
        """Clean up all resources. Call on application exit."""
        self.stop_processing()
        self._audio_engine.cleanup()
        self._midi_output.close()
        self._video_source.stop()
