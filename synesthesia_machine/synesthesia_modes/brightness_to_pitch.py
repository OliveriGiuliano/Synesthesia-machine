"""
Brightness-to-Pitch synesthesia mode.

Maps brightness/luminance bins to musical scale notes.
Darker regions map to lower notes, brighter regions map to higher notes.
"""

import cv2
import numpy as np
from typing import Dict, List
from synesthesia_machine.synesthesia_modes.base_mode import SynesthesiaMode, NoteEvent


class BrightnessToPitchMode(SynesthesiaMode):
    """
    Divides the brightness range (0-255) into N bins,
    where N = number of notes in the current musical scale.
    
    Each bin maps to one scale note (dark=low, bright=high).
    If a bin has enough pixels above the threshold, that note is played.
    Velocity is proportional to pixel count in that brightness range.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Parameters
        self._activation_threshold = 5.0      # Min percentage (0-100) of max bin pixels to trigger note
        self._velocity_scaling = 1.0          # Velocity multiplier
    
    def get_id(self) -> str:
        return "brightness_to_pitch"
    
    def get_name(self) -> str:
        return "Brightness → Pitch"
    
    def get_description(self) -> str:
        return ("Maps brightness levels to musical notes. Dark regions produce "
                "low notes, bright regions produce high notes.")
    
    def update_parameters(self, params: Dict[str, any]):
        super().update_parameters(params)
        if 'activation_threshold' in params:
            self._activation_threshold = max(0.0, min(100.0, float(params['activation_threshold'])))
        if 'velocity_scaling' in params:
            self._velocity_scaling = max(0.1, min(5.0, float(params['velocity_scaling'])))
    
    def get_parameters(self) -> Dict[str, any]:
        return {
            'activation_threshold': self._activation_threshold,
            'velocity_scaling': self._velocity_scaling,
        }
    
    def process_frame(self, frame_rgb: np.ndarray, scale_notes: List[int]) -> List[NoteEvent]:
        """
        Process a frame: convert to HSV, extract brightness (Value channel),
        bin brightness values, map to notes, generate events.
        
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
        v = frame_hsv[:, :, 2]  # Value/Brightness (0-255)
        
        # Compute brightness histogram across num_notes bins
        # Full 0-255 range divided evenly
        bin_width = 256.0 / num_notes
        bin_indices = (v.flatten() / bin_width).astype(np.int32)
        bin_indices = np.clip(bin_indices, 0, num_notes - 1)
        histogram = np.bincount(bin_indices, minlength=num_notes)[:num_notes]
        
        # Map histogram to notes
        # Threshold is a percentage (0-100) of the max pixels in any bin
        max_pixels = max(histogram) if max(histogram) > 0 else 1
        threshold_pixels = (self._activation_threshold / 100.0) * max_pixels
        desired_notes: Dict[int, int] = {}
        for i, pixel_count in enumerate(histogram):
            if pixel_count > threshold_pixels:
                note = scale_notes[i % num_notes]
                # Velocity: scale pixel count relative to max bin (logarithmic scaling)
                ratio = pixel_count / max_pixels
                velocity = min(127, int(20 + 107 * np.log1p(ratio * self._velocity_scaling) / np.log1p(1.0)))
                desired_notes[note] = velocity
        
        return self._update_active_notes(desired_notes)