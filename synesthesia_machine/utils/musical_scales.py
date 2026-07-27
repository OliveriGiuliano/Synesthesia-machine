"""
Musical scale definitions and utilities.
"""

from typing import List, Tuple

# Scale definitions: intervals in semitones from root
SCALE_DEFINITIONS = {
    "chromatic": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def get_scale_notes(scale_type: str, root_note: int, min_note: int = 24, max_note: int = 96) -> List[int]:
    """
    Get the MIDI note numbers for a given scale within [min_note, max_note] range.
    
    Args:
        scale_type: One of the keys in SCALE_DEFINITIONS
        root_note: MIDI note number for the root (e.g., 60 = C5)
        min_note: Minimum MIDI note number (0-127), default 24 (C1)
        max_note: Maximum MIDI note number (0-127), default 96 (C8)
    
    Returns:
        Sorted list of MIDI note numbers in the scale within the range.
    """
    if scale_type not in SCALE_DEFINITIONS:
        raise ValueError(f"Unknown scale type: {scale_type}. Choose from {list(SCALE_DEFINITIONS.keys())}")
    
    intervals = sorted(SCALE_DEFINITIONS[scale_type])
    root_midi = root_note % 12
    scale_notes = []
    
    # Walk through all notes in range, checking if they belong to the scale
    for midi in range(min_note, max_note + 1):
        note_in_octave = midi % 12
        interval = (note_in_octave - root_midi) % 12
        if interval in intervals:
            scale_notes.append(midi)
    
    return scale_notes


def get_note_name(midi_note: int) -> str:
    """Get the note name (e.g., 'C4') from a MIDI note number."""
    note_name = NOTE_NAMES[midi_note % 12]
    octave = (midi_note // 12) - 1
    return f"{note_name}{octave}"


def get_available_scales() -> List[str]:
    """Return list of available scale type names."""
    return list(SCALE_DEFINITIONS.keys())


def get_scale_note_count(scale_type: str) -> int:
    """Get the number of notes in a scale."""
    if scale_type not in SCALE_DEFINITIONS:
        raise ValueError(f"Unknown scale type: {scale_type}")
    return len(SCALE_DEFINITIONS[scale_type])


def midi_to_frequency(midi_note: int) -> float:
    """Convert MIDI note number to frequency in Hz."""
    return 440.0 * (2 ** ((midi_note - 69) / 12.0))


def frequency_to_midi(frequency: float) -> int:
    """Convert frequency in Hz to nearest MIDI note number."""
    if frequency <= 0:
        return 0
    import math
    return round(69 + 12 * math.log2(frequency / 440.0))