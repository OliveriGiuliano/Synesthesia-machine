"""
Base synesthesia mode - abstract interface for all synesthesia modes.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from PyQt6.QtCore import QObject, pyqtSignal


class NoteEvent:
    """Represents a note event to be sent to MIDI/audio output."""
    
    __slots__ = ['note', 'velocity', 'is_on']
    
    def __init__(self, note: int, velocity: int, is_on: bool):
        self.note = note        # MIDI note number (0-127)
        self.velocity = velocity  # Velocity (0-127)
        self.is_on = is_on      # True = NOTE ON, False = NOTE OFF
    
    def __repr__(self):
        state = "ON" if self.is_on else "OFF"
        return f"NoteEvent({state} note={self.note}, vel={self.velocity})"


class SynesthesiaMode(QObject):
    """
    Abstract base class for synesthesia modes.
    
    Each mode takes a video frame and produces MIDI note events.
    Modes maintain internal state (active notes) to ensure notes are
    held while conditions persist and released when they don't.
    
    Signals:
        events_ready: Emitted each frame with list of NoteEvents
        active_notes_changed: Emitted when the set of active notes changes
    
    Note: This class is abstract. Subclasses must implement:
        - get_id()
        - get_name()
        - get_description()
        - process_frame()
    """
    
    events_ready = pyqtSignal(object)  # List[NoteEvent]
    active_notes_changed = pyqtSignal(object)  # Dict[int, int] {note: velocity}
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_notes: Dict[int, int] = {}  # note -> velocity
        self._pending_events: List[NoteEvent] = []
    
    def get_id(self) -> str:
        """Return a unique identifier for this mode (e.g., 'color_to_note')."""
        raise NotImplementedError("Subclasses must implement get_id()")
    
    def get_name(self) -> str:
        """Return a human-readable name for this mode."""
        raise NotImplementedError("Subclasses must implement get_name()")
    
    def get_description(self) -> str:
        """Return a short description of what this mode does."""
        raise NotImplementedError("Subclasses must implement get_description()")
    
    def process_frame(self, frame_rgb: np.ndarray, scale_notes: List[int]) -> List[NoteEvent]:
        """
        Process a single video frame and return MIDI note events.
        
        Args:
            frame_rgb: RGB frame as numpy array (H, W, 3), values 0-255.
                       May be downscaled (e.g., 100x100).
            scale_notes: List of MIDI note numbers in the current musical scale.
        
        Returns:
            List of NoteEvent objects to be sent to the MIDI/audio output.
        """
        raise NotImplementedError("Subclasses must implement process_frame()")
    
    def update_parameters(self, params: Dict[str, any]):
        """
        Update mode-specific parameters.
        
        Args:
            params: Dictionary of parameter names to values.
        """
        pass
    
    def get_parameters(self) -> Dict[str, any]:
        """
        Return current mode-specific parameters.
        
        Returns:
            Dictionary of parameter names to current values.
        """
        return {}
    
    @property
    def active_notes(self) -> Dict[int, int]:
        """Return copy of currently active notes {note: velocity}."""
        return self._active_notes.copy()
    
    def clear_active_notes(self):
        """Clear all active notes. Call this on pause/stop."""
        events = []
        for note, velocity in self._active_notes.items():
            events.append(NoteEvent(note, 0, False))
        self._active_notes.clear()
        return events
    
    def _update_active_notes(self, desired_notes: Dict[int, int]) -> List[NoteEvent]:
        """
        Update internal active notes state and generate events for changes.
        
        Only generates events when notes are newly pressed or released.
        Velocity changes for already-active notes are silently tracked
        to avoid re-triggering sounds on every frame.
        
        Args:
            desired_notes: Dictionary of {note: velocity} that should be active.
        
        Returns:
            List of NoteEvent objects representing changes.
        """
        events = []
        current = set(self._active_notes.keys())
        desired = set(desired_notes.keys())
        
        # Notes to turn OFF (were active, no longer desired)
        for note in current - desired:
            events.append(NoteEvent(note, 0, False))
        
        # Notes to turn ON (newly desired)
        for note in desired - current:
            velocity = max(1, min(127, desired_notes[note]))
            events.append(NoteEvent(note, velocity, True))
        
        # Velocity changes for still-active notes: silently update
        # Do NOT send OFF+ON - this would re-trigger the sound every frame
        for note in current & desired:
            if desired_notes[note] != self._active_notes[note]:
                # Just update the stored velocity, no event generated
                pass
        
        self._active_notes = desired_notes.copy()
        self.active_notes_changed.emit(self._active_notes.copy())
        
        return events
