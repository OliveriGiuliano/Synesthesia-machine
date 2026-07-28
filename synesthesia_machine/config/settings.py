"""
Application settings and defaults.

Supports JSON serialization for persistence across sessions.
All settings include input validation with safe clamping and known-value
whitelists so that corrupted or out-of-range values never reach the engine.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# --- Known-value whitelists ---
VALID_VIDEO_SOURCE_TYPES = ("file", "camera")
VALID_MIDI_OUTPUT_TYPES = ("internal", "device")
VALID_SCALE_TYPES = ("chromatic", "major", "minor", "pentatonic_major", "pentatonic_minor")


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

    def validate(self) -> List[str]:
        """Validate and clamp values. Returns list of warnings."""
        warnings = []
        if self.source_type not in VALID_VIDEO_SOURCE_TYPES:
            warnings.append(f"Invalid source_type '{self.source_type}', using 'file'")
            self.source_type = "file"
        if self.camera_index < 0:
            warnings.append(f"camera_index {self.camera_index} < 0, clamped to 0")
            self.camera_index = 0
        if self.fps <= 0:
            warnings.append(f"fps {self.fps} <= 0, clamped to 1.0")
            self.fps = 1.0
        if self.fps > 240:
            warnings.append(f"fps {self.fps} > 240, clamped to 240")
            self.fps = 240.0
        for dim_name in ("processing_width", "processing_height"):
            val = getattr(self, dim_name)
            if val < 1:
                warnings.append(f"{dim_name} {val} < 1, clamped to 1")
                setattr(self, dim_name, 1)
            elif val > 1920:
                warnings.append(f"{dim_name} {val} > 1920, clamped to 1920")
                setattr(self, dim_name, 1920)
        return warnings


@dataclass
class MidiSettings:
    """MIDI output settings."""
    output_type: str = "internal"  # "internal" or "device"
    device_index: Optional[int] = None
    channel: int = 0  # MIDI channel 0-15

    def validate(self) -> List[str]:
        """Validate and clamp values. Returns list of warnings."""
        warnings = []
        if self.output_type not in VALID_MIDI_OUTPUT_TYPES:
            warnings.append(f"Invalid output_type '{self.output_type}', using 'internal'")
            self.output_type = "internal"
        if self.device_index is not None and self.device_index < 0:
            warnings.append(f"device_index {self.device_index} < 0, clamped to 0")
            self.device_index = 0
        if not 0 <= self.channel <= 15:
            warnings.append(f"channel {self.channel} out of [0,15], clamped")
            self.channel = max(0, min(15, self.channel))
        return warnings


@dataclass
class AudioSettings:
    """General audio settings."""
    volume: float = 0.75  # 0.0 to 1.0
    muted: bool = False
    base_octave: int = 4  # Base octave for note mapping

    def validate(self) -> List[str]:
        """Validate and clamp values. Returns list of warnings."""
        warnings = []
        if not 0.0 <= self.volume <= 1.0:
            warnings.append(f"volume {self.volume} out of [0,1], clamped")
            self.volume = max(0.0, min(1.0, self.volume))
        if not 1 <= self.base_octave <= 7:
            warnings.append(f"base_octave {self.base_octave} out of [1,7], clamped")
            self.base_octave = max(1, min(7, self.base_octave))
        return warnings


@dataclass
class ScaleSettings:
    """Musical scale settings."""
    scale_type: str = "chromatic"  # "chromatic", "major", "minor", "pentatonic_major", "pentatonic_minor"
    root_note: int = 60  # MIDI note number (60 = C5)

    def validate(self) -> List[str]:
        """Validate and clamp values. Returns list of warnings."""
        warnings = []
        if self.scale_type not in VALID_SCALE_TYPES:
            warnings.append(f"Invalid scale_type '{self.scale_type}', using 'chromatic'")
            self.scale_type = "chromatic"
        if not 0 <= self.root_note <= 127:
            warnings.append(f"root_note {self.root_note} out of [0,127], clamped")
            self.root_note = max(0, min(127, self.root_note))
        return warnings


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

    def validate(self) -> List[str]:
        """Validate and clamp values. Returns list of warnings."""
        warnings = []
        if not 0.0 <= self.activation_threshold <= 100.0:
            warnings.append(f"activation_threshold {self.activation_threshold} out of [0,100], clamped")
            self.activation_threshold = max(0.0, min(100.0, self.activation_threshold))
        if self.velocity_scaling <= 0:
            warnings.append(f"velocity_scaling {self.velocity_scaling} <= 0, clamped to 0.1")
            self.velocity_scaling = 0.1
        if self.velocity_scaling > 10:
            warnings.append(f"velocity_scaling {self.velocity_scaling} > 10, clamped to 10")
            self.velocity_scaling = 10.0
        if self.hue_bins < 1:
            warnings.append(f"hue_bins {self.hue_bins} < 1, clamped to 1")
            self.hue_bins = 1
        if self.hue_bins > 128:
            warnings.append(f"hue_bins {self.hue_bins} > 128, clamped to 128")
            self.hue_bins = 128
        for clamp_name, min_val, max_val in [
            ("saturation_min", 0, 255),
            ("value_min", 0, 255),
        ]:
            val = getattr(self, clamp_name)
            if not min_val <= val <= max_val:
                warnings.append(f"{clamp_name} {val} out of [{min_val},{max_val}], clamped")
                setattr(self, clamp_name, max(min_val, min(max_val, val)))
        return warnings


# Default settings file location relative to user's home/AppData
_SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".synesthesia_machine")
_SETTINGS_FILE = "settings.json"


def _get_settings_path() -> str:
    """Get the full path to the settings file."""
    return os.path.join(_SETTINGS_DIR, _SETTINGS_FILE)


@dataclass
class AppSettings:
    """Top-level application settings."""
    video: VideoSettings = field(default_factory=VideoSettings)
    midi: MidiSettings = field(default_factory=MidiSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    scale: ScaleSettings = field(default_factory=ScaleSettings)
    synesthesia: SynesthesiaSettings = field(default_factory=SynesthesiaSettings)
    visualizer_enabled: bool = True

    # --- Serialization ---

    def to_dict(self) -> Dict[str, Any]:
        """Serialize settings to a plain dictionary."""
        return {
            "video": asdict(self.video),
            "midi": asdict(self.midi),
            "audio": asdict(self.audio),
            "scale": asdict(self.scale),
            "synesthesia": asdict(self.synesthesia),
            "visualizer_enabled": self.visualizer_enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppSettings":
        """Deserialize settings from a dictionary, with safe defaults for missing keys."""
        settings = cls()

        # Video
        if "video" in data:
            v = data["video"]
            settings.video.source_type = v.get("source_type", settings.video.source_type)
            settings.video.file_path = v.get("file_path", settings.video.file_path)
            settings.video.camera_index = v.get("camera_index", settings.video.camera_index)
            settings.video.fps = v.get("fps", settings.video.fps)
            settings.video.processing_width = v.get("processing_width", settings.video.processing_width)
            settings.video.processing_height = v.get("processing_height", settings.video.processing_height)

        # MIDI
        if "midi" in data:
            m = data["midi"]
            settings.midi.output_type = m.get("output_type", settings.midi.output_type)
            settings.midi.device_index = m.get("device_index", settings.midi.device_index)
            settings.midi.channel = m.get("channel", settings.midi.channel)

        # Audio
        if "audio" in data:
            a = data["audio"]
            settings.audio.volume = float(a.get("volume", settings.audio.volume))
            settings.audio.muted = a.get("muted", settings.audio.muted)
            settings.audio.base_octave = a.get("base_octave", settings.audio.base_octave)

        # Scale
        if "scale" in data:
            s = data["scale"]
            settings.scale.scale_type = s.get("scale_type", settings.scale.scale_type)
            settings.scale.root_note = s.get("root_note", settings.scale.root_note)

        # Synesthesia
        if "synesthesia" in data:
            sy = data["synesthesia"]
            settings.synesthesia.active_mode = sy.get("active_mode", settings.synesthesia.active_mode)
            settings.synesthesia.activation_threshold = sy.get("activation_threshold", settings.synesthesia.activation_threshold)
            settings.synesthesia.velocity_scaling = sy.get("velocity_scaling", settings.synesthesia.velocity_scaling)
            settings.synesthesia.hue_bins = sy.get("hue_bins", settings.synesthesia.hue_bins)
            settings.synesthesia.saturation_min = sy.get("saturation_min", settings.synesthesia.saturation_min)
            settings.synesthesia.value_min = sy.get("value_min", settings.synesthesia.value_min)

        # Top-level
        settings.visualizer_enabled = data.get("visualizer_enabled", settings.visualizer_enabled)

        # Always validate after loading to catch corrupted/out-of-range values
        settings.validate()
        return settings

    def validate(self) -> List[str]:
        """Validate all sub-settings. Returns combined list of warnings."""
        warnings = []
        warnings.extend(self.video.validate())
        warnings.extend(self.midi.validate())
        warnings.extend(self.audio.validate())
        warnings.extend(self.scale.validate())
        warnings.extend(self.synesthesia.validate())
        if warnings:
            logger.warning("Settings validation warnings: %s", warnings)
        return warnings

    # --- File I/O ---

    def save_to_file(self, path: Optional[str] = None):
        """Save settings to a JSON file."""
        save_path = path or _get_settings_path()
        parent_dir = os.path.dirname(save_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_file(cls, path: Optional[str] = None) -> "AppSettings":
        """Load settings from a JSON file. Returns defaults if file doesn't exist."""
        load_path = path or _get_settings_path()
        if not os.path.exists(load_path):
            return cls()
        try:
            with open(load_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to load settings from %s: %s. Using defaults.", load_path, e)
            return cls()
