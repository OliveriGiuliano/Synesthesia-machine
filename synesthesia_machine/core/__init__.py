# Core modules - video, MIDI, audio engines, and central processing engine
from synesthesia_machine.core.engine import SynesthesiaEngine
from synesthesia_machine.core.video_source import VideoSource
from synesthesia_machine.core.midi_output import MidiOutput
from synesthesia_machine.core.audio_engine import AudioEngine

__all__ = ['SynesthesiaEngine', 'VideoSource', 'MidiOutput', 'AudioEngine']
