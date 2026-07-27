"""
Application settings and defaults.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class VideoSettings:
    """Video source settings."""
    source_type: str = "file"  # "file" or "camera"
    file_path: Optional[str] = None
    camera_index: int = 0
    fps: float = 24.0
    processing_width: int = 100
    processing_height: int = 100
    is_playing: bool = False


@dataclass
class MidiSettings:
    """MIDI output settings."""
    output_type: str = "internal"  # "internal" or "device"
    device_index: Optional[int] = None
    channel: int = 0  # MIDI channel 0-15


@dataclass
class AudioSettings:
    """General audio settings."""
    volume: float = 0.75  # 0.0 to 1.0
    muted: bool = False
    base_octave: int = 4  # Base octave for note mapping


@dataclass
class ScaleSettings:
    """Musical scale settings."""
    scale_type: str = "chromatic"  # "chromatic", "major", "minor", "pentatonic_major", "pentatonic_minor"
    root_note: int = 60  # MIDI note number (60 = C5)


@dataclass
class SynesthesiaSettings:
    """Synesthesia mode settings."""
    active_mode: str = "color_to_note"  # Current mode identifier
    activation_threshold: float = 5.0  # Minimum percentage (0-100) of dominant color to trigger a note
    velocity_scaling: float = 1.0  # Multiplier for velocity calculation
    # Color-to-note specific
    hue_bins: int = 12  # Number of hue bins (auto-set based on scale)
    saturation_min: int = 20  # Minimum saturation to consider a pixel (0-255)
    value_min: int = 50  # Minimum brightness to consider a pixel (0-255)


@dataclass
class AppSettings:
    """Top-level application settings."""
    video: VideoSettings = field(default_factory=VideoSettings)
    midi: MidiSettings = field(default_factory=MidiSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    scale: ScaleSettings = field(default_factory=ScaleSettings)
    synesthesia: SynesthesiaSettings = field(default_factory=SynesthesiaSettings)
    visualizer_enabled: bool = True