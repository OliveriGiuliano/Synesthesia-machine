"""Mock MIDI enumeration/send acceptance tests."""

from tools.midi_probe import MidiMessage, MockMidiBackend


def test_mock_midi_backend_enumerates_and_records_send() -> None:
    backend = MockMidiBackend(ports=("Loopback A", "Loopback B"))
    message = MidiMessage(type="note_on", channel=0, note=69, velocity=40)

    assert tuple(backend.output_names()) == ("Loopback A", "Loopback B")
    backend.send("Loopback B", message)
    assert backend.sent == [("Loopback B", message)]
