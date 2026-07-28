"""
Motion-to-Pitch synesthesia mode.

Detects frame-to-frame motion, then maps the hue of moving pixels
to musical scale notes. Stationary pixels are ignored.
"""

import cv2
import numpy as np
from typing import Dict, List, Optional
from synesthesia_machine.synesthesia_modes.base_mode import SynesthesiaMode, NoteEvent


class MotionToPitchMode(SynesthesiaMode):
    """
    Computes absolute difference between consecutive frames to detect motion.
    Pixels that changed above a threshold keep their hue value; stationary
    pixels are discarded. The hue spectrum of moving pixels is then binned
    across the scale notes (same logic as Color→Pitch).
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Parameters
        self._activation_threshold = 5.0      # Min % of max bin to trigger note
        self._velocity_scaling = 1.0          # Velocity multiplier
        self._motion_threshold = 30           # Min pixel diff to count as motion (0-255)
        self._saturation_min = 20             # Min saturation to filter grayscale
        self._value_min = 50                  # Min brightness to filter dark pixels

        # State
        self._prev_gray: Optional[np.ndarray] = None

    def get_id(self) -> str:
        return "motion_to_pitch"

    def get_name(self) -> str:
        return "Motion → Pitch"

    def get_description(self) -> str:
        return ("Detects motion between frames and maps the hue of moving "
                "pixels to musical notes. Stationary areas produce no sound.")

    def update_parameters(self, params: Dict[str, any]):
        super().update_parameters(params)
        if 'activation_threshold' in params:
            self._activation_threshold = max(0.0, min(100.0, float(params['activation_threshold'])))
        if 'velocity_scaling' in params:
            self._velocity_scaling = max(0.1, min(5.0, float(params['velocity_scaling'])))
        if 'motion_threshold' in params:
            self._motion_threshold = max(0, min(255, int(params['motion_threshold'])))
        if 'saturation_min' in params:
            self._saturation_min = max(0, min(255, int(params['saturation_min'])))
        if 'value_min' in params:
            self._value_min = max(0, min(255, int(params['value_min'])))

    def get_parameters(self) -> Dict[str, any]:
        return {
            'activation_threshold': self._activation_threshold,
            'velocity_scaling': self._velocity_scaling,
            'motion_threshold': self._motion_threshold,
            'saturation_min': self._saturation_min,
            'value_min': self._value_min,
        }

    def process_frame(self, frame_rgb: np.ndarray, scale_notes: List[int]) -> List[NoteEvent]:
        """
        Detect motion via frame difference, then bin hues of moving pixels
        and map to scale notes.

        Args:
            frame_rgb: RGB frame (H, W, 3), values 0-255
            scale_notes: List of MIDI note numbers for the current scale

        Returns:
            List of NoteEvents to send to MIDI/audio output.
        """
        num_notes = len(scale_notes)
        if num_notes == 0:
            return []

        # Convert to grayscale for motion detection
        frame_gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)

        # Compute motion mask via absolute difference
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, frame_gray)
            motion_mask = diff >= self._motion_threshold
        else:
            # No previous frame — nothing to compare, skip
            self._prev_gray = frame_gray.copy()
            return self._update_active_notes({})

        # Update previous frame
        self._prev_gray = frame_gray.copy()

        # Convert to HSV for hue extraction
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        frame_hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        h = frame_hsv[:, :, 0]  # Hue (0-180)
        s = frame_hsv[:, :, 1]  # Saturation (0-255)
        v = frame_hsv[:, :, 2]  # Value (0-255)

        # Combine masks: motion AND saturation AND brightness
        color_mask = (s >= self._saturation_min) & (v >= self._value_min)
        combined_mask = motion_mask & color_mask

        masked_hues = h[combined_mask]

        if masked_hues.size == 0:
            return self._update_active_notes({})

        # Bin hues across num_notes bins (same as Color→Pitch)
        bin_width = 180.0 / num_notes
        bin_indices = (masked_hues / bin_width).astype(np.int32)
        bin_indices = np.clip(bin_indices, 0, num_notes - 1)
        histogram = np.bincount(bin_indices, minlength=num_notes)[:num_notes]

        # Map histogram to notes
        max_pixels = max(histogram) if max(histogram) > 0 else 1
        threshold_pixels = (self._activation_threshold / 100.0) * max_pixels
        desired_notes: Dict[int, int] = {}
        for i, pixel_count in enumerate(histogram):
            if pixel_count > threshold_pixels:
                note = scale_notes[i % num_notes]
                ratio = pixel_count / max_pixels
                velocity = min(127, int(20 + 107 * np.log1p(ratio * self._velocity_scaling) / np.log1p(1.0)))
                desired_notes[note] = velocity

        return self._update_active_notes(desired_notes)