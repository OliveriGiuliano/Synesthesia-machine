# MIDI package

Scale selection, exact-port MIDI reconciliation, and the optional callback-safe debug synthesizer.

Graph values describe complete desired note state. Dedicated service threads own enumeration, port
or audio-device opening, sends, panic, and close. New MIDI catalogue entries separate raw backend IDs
from friendly labels; legacy saved labels remain accepted when unambiguous. New audio selections use
`sounddevice:<index>`, with an empty ID representing the system default.

Use the `synesthesia_machine.midi` facade. Keep backends injectable and cover disappearance, send
failure, panic, repeated close, and unavailable-device behavior without real hardware.
