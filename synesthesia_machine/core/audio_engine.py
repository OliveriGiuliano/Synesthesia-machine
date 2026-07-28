"""
Audio engine module - real-time synthesis using sounddevice.
Uses phase-continuous sine wave synthesis for smooth, pop-free audio.
"""

import numpy as np
import threading
from typing import Dict
from PyQt6.QtCore import QObject, pyqtSignal

_sd = None
_sample_rate = 48000


def _init_sd():
    """Initialize sounddevice with graceful fallback.
    
    Tries to query the default output device for sample rate.
    Falls back to 48000 Hz if the device is unavailable or query fails.
    """
    global _sd, _sample_rate
    if _sd is not None:
        return True
    try:
        import sounddevice as sd
        _sd = sd
        # Try to get sample rate from default output device
        try:
            default_device_index = sd.default.device[1]
            if default_device_index is not None:
                default_out = sd.query_devices(default_device_index, 'output')
                _sample_rate = int(default_out['default_samplerate']) or 48000
        except Exception:
            # query_devices can fail if default device was disconnected
            _sample_rate = 48000
        return True
    except Exception as e:
        print(f"Warning: sounddevice not available: {e}")
        return False


class Voice:
    """Simple oscillator voice with phase tracking and release state."""
    __slots__ = ['freq', 'velocity', 'phase', 'releasing']

    def __init__(self, freq: float, velocity: float):
        self.freq = freq
        self.velocity = velocity
        self.phase = 0.0
        self.releasing = False


class AudioEngine(QObject):
    """
    Real-time audio synthesis engine using sounddevice.
    Single output callback mixes all active voices with smooth envelopes.
    """

    notes_changed = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._volume = 0.5
        self._muted = False
        self._voices: Dict[int, Voice] = {}
        self._stream = None
        self._lock = threading.Lock()
        self._attack_remaining: Dict[int, int] = {}
        self._release_remaining: Dict[int, int] = {}
        # Envelope lengths in samples (at 48kHz: 10ms attack, 80ms release)
        self._attack_samples = 480
        self._release_samples = 3840

    def _ensure_stream(self) -> bool:
        if not _init_sd():
            return False
        if self._stream is not None:
            try:
                if self._stream.active:
                    return True
            except Exception:
                pass
        try:
            self._stream = _sd.OutputStream(
                channels=1,
                samplerate=_sample_rate,
                dtype='float32',
                callback=self._audio_callback,
                blocksize=1024,
            )
            self._stream.start()
            return True
        except Exception as e:
            self.error_occurred.emit(f"Audio stream error: {e}")
            return False

    def _audio_callback(self, outdata, frames,
                        time_info, status):
        """Synthesize and mix all active voices into outdata.

        Voices in release mode are faded out and removed once the release
        envelope finishes, so stop_note() produces a smooth fade instead of
        an abrupt cutoff.

        IMPORTANT: The entire callback runs under self._lock to prevent a race
        condition where stop_note() or play_note() modify _release_remaining
        or _attack_remaining between the callback's read and write-back phases.
        The previous two-phase-lock pattern (copy→process→overwrite) caused
        stop_note's release state to be silently lost.
        """
        # Hold lock for the entire callback to prevent write-back from
        # overwriting concurrent stop_note/play_note changes.
        # With max ~12-24 voices and numpy operations, this is fast enough
        # that audio dropouts won't occur.
        done_release_notes = []

        with self._lock:
            voices_list = list(self._voices.items())
            vol = self._volume if not self._muted else 0.0

            # Zero output buffer (outdata shape: (frames, 1))
            out = outdata[:, 0]
            out[:] = 0.0

            for midi_note, voice in voices_list:
                a_rem = self._attack_remaining.get(midi_note, 0)
                r_rem = self._release_remaining.get(midi_note, 0)

                if a_rem > 0:
                    start_env = 1.0 - min(1.0, a_rem / self._attack_samples)
                    end_env = 1.0 - max(0, (a_rem - frames) / self._attack_samples)
                elif r_rem > 0:
                    start_env = min(1.0, r_rem / self._release_samples)
                    end_env = max(0, (r_rem - frames) / self._release_samples)
                else:
                    start_env = 1.0
                    end_env = 1.0

                # Generate sine wave samples
                phase_start = voice.phase
                phase_end = phase_start + voice.freq * frames / _sample_rate
                phases = np.linspace(phase_start, phase_end, frames, endpoint=False)
                voice.phase = phase_end % 1.0

                samples = np.sin(2 * np.pi * phases)

                # Apply envelope ramp
                if a_rem > 0 or r_rem > 0:
                    env_ramp = np.linspace(start_env, end_env, frames)
                else:
                    env_ramp = np.ones(frames)

                gain = voice.velocity * vol * 0.3
                out += samples * env_ramp * gain

                # Update attack/release counters directly on the live dict
                if a_rem > 0:
                    self._attack_remaining[midi_note] = max(0, a_rem - frames)
                elif r_rem > 0:
                    self._release_remaining[midi_note] = max(0, r_rem - frames)
                    if self._release_remaining[midi_note] == 0:
                        done_release_notes.append(midi_note)

            # Soft clipping
            np.clip(out, -1.0, 1.0, out=out)

            # Remove voices whose release envelope has finished
            for note in done_release_notes:
                self._voices.pop(note, None)
                self._release_remaining.pop(note, None)

        # Emit signal outside the lock to avoid blocking the audio callback
        if done_release_notes:
            active = sorted(self._voices.keys())
            self.notes_changed.emit(active)

    def play_note(self, midi_note: int, velocity: int = 64):
        with self._lock:
            if midi_note in self._voices:
                voice = self._voices[midi_note]
                if voice.releasing:
                    # Note is in release envelope from a recent stop_note().
                    # Re-trigger it: cancel the release and restart normal playing.
                    voice.releasing = False
                    voice.velocity = velocity / 127.0
                    self._attack_remaining.pop(midi_note, None)
                    self._release_remaining.pop(midi_note, None)
                    return
                else:
                    # Note already playing, just update velocity
                    voice.velocity = velocity / 127.0
                    self._attack_remaining.pop(midi_note, None)
                    self._release_remaining.pop(midi_note, None)
                    return

            freq = 440.0 * (2 ** ((midi_note - 69) / 12.0))
            self._voices[midi_note] = Voice(freq, velocity / 127.0)
            self._attack_remaining[midi_note] = self._attack_samples
            self._release_remaining.pop(midi_note, None)

        self._ensure_stream()
        active = sorted(self._voices.keys())
        self.notes_changed.emit(active)

    def stop_note(self, midi_note: int):
        with self._lock:
            voice = self._voices.pop(midi_note, None)
            self._attack_remaining.pop(midi_note, None)
            self._release_remaining.pop(midi_note, None)

            if voice is not None:
                # Mark voice as releasing and put it back so the audio
                # callback can fade it out instead of cutting it off.
                voice.releasing = True
                self._voices[midi_note] = voice
                self._release_remaining[midi_note] = self._release_samples

        # Note: we intentionally do NOT emit notes_changed here.
        # The voice is still "active" during the release envelope and
        # will be removed by the audio callback when the fade finishes.

    def stop_all(self):
        with self._lock:
            self._voices.clear()
            self._attack_remaining.clear()
            self._release_remaining.clear()
        self.notes_changed.emit([])

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, volume))

    def set_muted(self, muted: bool):
        self._muted = muted

    @property
    def active_notes(self):
        with self._lock:
            return sorted(self._voices.keys())

    def cleanup(self):
        self.stop_all()
        if self._stream is not None:
            try:
                self._stream.abort()
            except Exception:
                pass
            self._stream = None