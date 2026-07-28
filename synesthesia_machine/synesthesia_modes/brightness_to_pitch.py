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
        self._saturation_min = 10             # Min saturation to filter near-grayscale pixels (0-255)
        self._min_brightness = 0              # Ignore pixels darker than this (0-255)
        self._max_brightness = 255            # Ignore pixels brighter than this (0-255)
    
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
        if 'saturation_min' in params:
            self._saturation_min = max(0, min(255, int(params['saturation_min'])))
        if 'min_brightness' in params:
            self._min_brightness = max(0, min(255, int(params['min_brightness'])))
        if 'max_brightness' in params:
            self._max_brightness = max(0, min(255, int(params['max_brightness'])))
    
    def get_parameters(self) -> Dict[str, any]:
        return {
            'activation_threshold': self._activation_threshold,
            'velocity_scaling': self._velocity_scaling,
            'saturation_min': self._saturation_min,
            'min_brightness': self._min_brightness,
            'max_brightness': self._max_brightness,
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
        s = frame_hsv[:, :, 1]  # Saturation (0-255)
        v = frame_hsv[:, :, 2]  # Value/Brightness (0-255)
        
        # Mask: only consider pixels with enough saturation and in brightness range
        mask = (s >= self._saturation_min) & (v >= self._min_brightness) & (v <= self._max_brightness)
        masked_brightness = v[mask]
        
        if masked_brightness.size == 0:
            return self._update_active_notes({})
        
        # Compute brightness histogram across num_notes bins
        # Each bin covers (max_brightness - min_brightness) / num_notes range
        brightness_range = self._max_brightness - self._min_brightness + 1
        bin_width = brightness_range / num_notes
        bin_indices = ((masked_brightness - self._min_brightness) / bin_width).astype(np.int32)
        bin_indices = np.clip(bin_indices, 0, num_notes - 1)
        histogram = np.bincount(bin_indices, minlength=num_notes)[:num_notes]
        
        # Map histogram to desired notes (low brightness = low notes, high = high)
        max_pixels = max(histogram) if max(histogram) > 0 else 1
        threshold_pixels = (self._activation_threshold / 100.0) * max_pixels
        desired_notes: Dict[int, int] = {}
        for i, pixel_count in enumerate(histogram):
            if pixel_count > threshold_pixels:
                note = scale_notes[i % num_notes]
                # Velocity: scale pixel count relative to max bin
                ratio = pixel_count / max_pixels
                velocity = min(127, int(20 + 107 * np.log1p(ratio * self._velocity_scaling) / np.log1p(1.0)))
                desired_notes[note] = velocity
        
        return self._update_active_notes(desired_notes)