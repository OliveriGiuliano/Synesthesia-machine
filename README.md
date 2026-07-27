# Synesthesia Machine

A program that takes video input and outputs MIDI instructions. Video frames are analyzed and mapped to musical notes based on the selected synesthesia mode.

## Features

- **Video Input**: Load video files or connect a webcam/live camera
- **Real-time Analysis**: Processes video frames at 24+ FPS (using downscaled 100x100 frames)
- **MIDI Output**: Send to virtual MIDI outputs, physical MIDI devices, or internal piano player
- **Visualizer**: See which notes are being fired in real-time
- **Synesthesia Modes**: Multiple modes for mapping visual data to music
- **Customizable**: Adjust thresholds, scales, and parameters per mode

## Installation

### Prerequisites

- Python 3.8 or higher
- Conda (recommended) or pip

### Using Conda (Recommended)

```bash
# Create a new conda environment
conda create -n synesthesia-test python=3.11 -y

# Activate the environment
conda activate synesthesia-test

# Install dependencies
pip install -r requirements.txt
```

### Using pip

```bash
# Create a virtual environment
python -m venv venv

# Activate the environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

## Quick Start Guide

1. **Load a video source**: Click "Load Video" to select a video file, or "Use Webcam" to connect a camera
2. **Play the video**: Use the video controls (Play/Pause, Forward, Backward)
3. **Select a synesthesia mode**: Choose from available modes in the mode selector
4. **Customize parameters**: Adjust threshold and other settings for the selected mode
5. **Configure audio**: Set volume, musical scale, and mute options
6. **Select MIDI output**: Choose a MIDI output device, or leave unselected for internal playback
7. **Enable visualizer**: Check the visualizer option to see notes being fired

## Synesthesia Modes

### Color to Note (Default)

Maps colors to musical notes:
- Divides the hue spectrum (0-360°) by the number of notes in the selected musical scale
- Each hue bin corresponds to one note
- If enough pixels in a frame match a hue bin (above threshold), that note is played
- Velocity is determined by how many pixels exceed the threshold

## Project Structure

```
synesthesia_machine/
├── __init__.py
├── config/
│   ├── __init__.py
│   └── settings.py          # Application configuration
├── core/
│   ├── __init__.py
│   ├── video_source.py       # Video input handling (file/webcam)
│   ├── midi_output.py        # MIDI output handling
│   └── audio_engine.py       # Audio playback and sound engine
├── gui/
│   ├── __init__.py
│   ├── main_window.py        # Main application window
│   └── visualizer.py         # Note visualizer widget
├── synesthesia_modes/
│   ├── __init__.py
│   ├── base_mode.py          # Abstract base class for modes
│   └── color_to_note.py      # Color-to-note mapping mode
└── utils/
    ├── __init__.py
    ├── musical_scales.py     # Musical scale definitions
    └── color_analysis.py     # Color analysis utilities
main.py                       # Application entry point
requirements.txt              # Python dependencies
```

## Adding New Synesthesia Modes

1. Create a new file in `synesthesia_modes/`
2. Inherit from `SynesthesiaMode` (base_mode.py)
3. Implement required methods:
   - `process_frame(frame)`: Analyze frame and return note events
   - `get_settings_widget()`: Return GUI widget for mode-specific settings
4. Register the mode in the GUI

## Dependencies

- **PyQt6**: GUI framework
- **OpenCV**: Video processing and computer vision
- **NumPy**: Numerical operations and array processing
- **Python-RTMIDI**: MIDI output support
- **Pygame**: Audio playback

## Technical Details

- Frame processing uses 100x100 pixel downscaled images for performance
- Real-time processing targets 24+ FPS
- Notes are held while conditions are met (not triggered every frame)
- Supports multiple musical scales (major, minor, pentatonic, chromatic, etc.)

## License

MIT License