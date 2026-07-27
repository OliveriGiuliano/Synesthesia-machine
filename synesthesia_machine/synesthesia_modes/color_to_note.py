"""
Color-to-Note synesthesia mode.

Maps hue bins to musical scale notes. Each hue range corresponds to one note.
Pixel count above threshold determines note activation and velocity.
"""

import cv2
import numpy as np
from typing import Dict, List
from synesthesia_machine.synesthesia_modes.base_mode import SynesthesiaMode, NoteEvent


class ColorToNoteMode(SynesthesiaMode):
    """
    Divides the hue spectrum (0-180 in OpenCV HSV) into N bins,
    where N = number of notes in the current musical scale.
    
    Each bin maps to one scale note. If a bin has enough pixels above
    the threshold, that note is played. Velocity is proportional to
    how many pixels exceed the threshold.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Parameters
        self._activation_threshold = 5.0      # Min percentage (0-100) of max bin pixels to trigger note
        self._velocity_scaling = 1.0          # Velocity multiplier
        self._saturation_min = 20             # Min saturation (0-255)
        self._value_min = 50                  # Min brightness (0-255)
    
    def get_id(self) -> str:
        return "color_to_note"
    
    def get_name(self) -> str:
        return "Color → Note"
    
    def get_description(self) -> str:
        return ("Maps colors to musical notes. Each hue range is assigned "
                "a note from the selected scale. More pixels of a color "
                "produce a louder note.")
    
    def update_parameters(self, params: Dict[str, any]):
        super().update_parameters(params)
        if 'activation_threshold' in params:
            self._activation_threshold = max(0.0, min(100.0, float(params['activation_threshold'])))
        if 'velocity_scaling' in params:
            self._velocity_scaling = max(0.1, min(5.0, float(params['velocity_scaling'])))
        if 'saturation_min' in params:
            self._saturation_min = max(0, min(255, int(params['saturation_min'])))
        if 'value_min' in params:
            self._value_min = max(0, min(255, int(params['value_min'])))
    
    def get_parameters(self) -> Dict[str, any]:
        return {
            'activation_threshold': self._activation_threshold,
            'velocity_scaling': self._velocity_scaling,
            'saturation_min': self._saturation_min,
            'value_min': self._value_min,
        }
    
    def process_frame(self, frame_rgb: np.ndarray, scale_notes: List[int]) -> List[NoteEvent]:
        """
        Process a frame: convert to HSV, bin hues, map to notes, generate events.
        
        Args:
            frame_rgb: RGB frame (H, W, 3), values 0-255
            scale_notes: List of MIDI note numbers for the current scale
        
        Returns:
            List of NoteEvents to send to MIDI/audio output.
        """
        num_notes = len(scale_notes)
        if num_notes == 0:
            return []
        
        # Convert RGB -> BGR -> HSV (OpenCV convention)
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        frame_hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        
        # Extract channels
        h = frame_hsv[:, :, 0]  # Hue (0-180)
        s = frame_hsv[:, :, 1]  # Saturation (0-255)
        v = frame_hsv[:, :, 2]  # Value (0-255)
        
        # Mask: only consider pixels with enough saturation and brightness
        mask = (s >= self._saturation_min) & (v >= self._value_min)
        masked_hues = h[mask]
        
        # Compute hue histogram across num_notes bins
        # Each bin covers 180.0 / num_notes degrees
        bin_width = 180.0 / num_notes
        bin_indices = (masked_hues / bin_width).astype(np.int32)
        bin_indices = np.clip(bin_indices, 0, num_notes - 1)
        histogram = np.bincount(bin_indices, minlength=num_notes)[:num_notes]
        
        # Map histogram to desired notes
        # Threshold is a percentage (0-100) of the max pixels in any bin
        max_pixels = max(histogram) if max(histogram) > 0 else 1
        threshold_pixels = (self._activation_threshold / 100.0) * max_pixels
        desired_notes: Dict[int, int] = {}
        for i, pixel_count in enumerate(histogram):
            if pixel_count >= threshold_pixels:
                note = scale_notes[i % num_notes]
                # Velocity: scale pixel count relative to max bin (percentage of dominant color)
                # Use logarithmic scaling for more natural response
                ratio = pixel_count / max_pixels  # 0.0 to 1.0
                velocity = min(127, int(20 + 107 * np.log1p(ratio * self._velocity_scaling) / np.log1p(1.0)))
                desired_notes[note] = velocity
        
        # Generate events from state change
        events = self._update_active_notes(desired_notes)
        
        if events:
            self.events_ready.emit(events)
        
        return events