"""
MIDI output module - handles MIDI device output and internal playback.
"""

import logging
from typing import Optional, List, Tuple
from PyQt6.QtCore import QObject, pyqtSignal
import rtmidi

logger = logging.getLogger(__name__)


class MidiOutput(QObject):
    """
    MIDI output handler that can send to a physical/virtual MIDI device
    or route to an internal callback for pygame playback.
    
    Signals:
        note_on_received: Emitted when a NOTE ON is sent (note, velocity, channel)
        note_off_received: Emitted when a NOTE OFF is sent (note, channel)
    """
    
    note_on_received = pyqtSignal(int, int, int)  # note, velocity, channel
    note_off_received = pyqtSignal(int, int)  # note, channel
    error_occurred = pyqtSignal(str)
    devices_updated = pyqtSignal(list)  # List of (index, name) tuples
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._midi_out: Optional[rtmidi.MidiOut] = None
        self._active: bool = True
        self._channel: int = 0
        self._device_list: List[Tuple[int, str]] = []
        self._port_open: bool = False
        self._discover_devices()
    
    def _discover_devices(self):
        """Discover available MIDI output devices."""
        self._device_list = []
        try:
            midi = rtmidi.MidiOut()
            try:
                count = midi.get_port_count()
                for i in range(count):
                    name = midi.get_port_name(i)
                    self._device_list.append((i, name))
            finally:
                # Don't call close_port() since no port was opened —
                # just let the object be garbage collected
                del midi
        except Exception as e:
            self.error_occurred.emit(f"Error discovering MIDI devices: {e}")
        
        self.devices_updated.emit(self._device_list)
    
    def get_available_devices(self) -> List[Tuple[int, str]]:
        """Return list of (index, name) for available MIDI devices."""
        return self._device_list.copy()
    
    def open_device(self, device_index: int) -> bool:
        """Open a specific MIDI output device."""
        self._close()
        
        try:
            self._midi_out = rtmidi.MidiOut()
            if self._midi_out.get_port_count() > 0 and device_index < self._midi_out.get_port_count():
                self._midi_out.open_port(device_index)
                self._port_open = True
                self._active = True
                return True
            else:
                self.error_occurred.emit(f"MIDI device index {device_index} not found")
                return False
        except Exception as e:
            self.error_occurred.emit(f"Failed to open MIDI device: {e}")
            return False
    
    def close(self):
        """Close the MIDI output."""
        self._close()
    
    def _close(self):
        """Internal close method."""
        self._active = False
        if self._midi_out is not None:
            if self._port_open:
                try:
                    self._midi_out.close_port()
                except Exception as e:
                    logger.debug("Error closing MIDI port: %s", e)
            self._port_open = False
            self._midi_out = None
    
    def set_channel(self, channel: int):
        """Set the MIDI channel (0-15)."""
        self._channel = max(0, min(15, channel))
    
    def send_note_on(self, note: int, velocity: int = 64):
        """
        Send a NOTE ON message.
        
        Args:
            note: MIDI note number (0-127)
            velocity: Velocity (0-127). If 0, sends NOTE OFF instead.
        """
        if not self._active:
            return
        
        note = max(0, min(127, note))
        velocity = max(0, min(127, velocity))
        
        if velocity == 0:
            self.send_note_off(note)
            return
        
        # Send to MIDI device if connected
        if self._midi_out is not None:
            try:
                self._midi_out.send_message([0x90 | self._channel, note, velocity])
            except Exception as e:
                self.error_occurred.emit(f"MIDI send error: {e}")
        
        # Always emit internal signal for visualizer/internal playback
        self.note_on_received.emit(note, velocity, self._channel)
    
    def send_note_off(self, note: int, velocity: int = 0):
        """
        Send a NOTE OFF message.
        
        Args:
            note: MIDI note number (0-127)
            velocity: Off velocity (0-127), typically 0.
        """
        if not self._active:
            return
        
        note = max(0, min(127, note))
        
        # Send to MIDI device if connected
        if self._midi_out is not None:
            try:
                self._midi_out.send_message([0x80 | self._channel, note, velocity])
            except Exception as e:
                self.error_occurred.emit(f"MIDI send error: {e}")
        
        # Always emit internal signal
        self.note_off_received.emit(note, self._channel)
    
    def send_control_change(self, controller: int, value: int):
        """Send a Control Change message."""
        if not self._active or self._midi_out is None:
            return
        
        controller = max(0, min(127, controller))
        value = max(0, min(127, value))
        
        try:
            self._midi_out.send_message([0xB0 | self._channel, controller, value])
        except Exception as e:
            self.error_occurred.emit(f"MIDI CC error: {e}")
    
    def is_device_connected(self) -> bool:
        """Check if a MIDI device is currently connected."""
        return self._midi_out is not None
    
    def refresh_devices(self):
        """Re-scan for available MIDI devices."""
        self._discover_devices()
    
    def __del__(self):
        """Cleanup on destruction."""
        self._close()


if __name__ == "__main__":
    # Quick test
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    midi = MidiOutput()
    midi.devices_updated.connect(lambda devices: print("Available devices:", devices))
    
    if len(midi.get_available_devices()) > 0:
        print(f"Opening first device: {midi.get_available_devices()[0]}")
        midi.open_device(0)
        midi.send_note_on(60, 100)
        import time
        time.sleep(0.5)
        midi.send_note_off(60)
        midi.close()
    
    print("MIDI test complete.")