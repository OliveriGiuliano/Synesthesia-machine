"""Shared desired-state diff: set difference, order, and key-type freedom."""

from __future__ import annotations

from synesthesia_machine.contracts import MidiNoteKey
from synesthesia_machine.midi import MidiStateDiff, diff_midi_states


def _key(channel: int, note: int) -> MidiNoteKey:
    return MidiNoteKey(channel, note)


def test_added_notes_carry_current_velocity_in_current_order() -> None:
    diff = diff_midi_states(
        {_key(0, 60): 80},
        {_key(0, 60): 80, _key(0, 62): 90, _key(1, 64): 100},
    )
    assert diff.removed == ()
    assert diff.added == ((_key(0, 62), 90), (_key(1, 64), 100))


def test_removed_keys_come_out_in_previous_order() -> None:
    diff = diff_midi_states(
        {_key(0, 60): 80, _key(0, 62): 90, _key(1, 64): 100},
        {_key(1, 64): 100},
    )
    assert diff.removed == (_key(0, 60), _key(0, 62))
    assert diff.added == ()


def test_keys_held_in_both_states_are_never_part_of_the_diff() -> None:
    diff = diff_midi_states({_key(0, 60): 50}, {_key(0, 60): 120})
    assert diff == MidiStateDiff(removed=(), added=())


def test_mixed_transition_keeps_per_side_insertion_order() -> None:
    diff = diff_midi_states(
        {_key(0, 58): 70, _key(0, 60): 80, _key(0, 64): 90},
        {_key(0, 64): 90, _key(0, 72): 100, _key(0, 76): 110},
    )
    assert diff.removed == (_key(0, 58), _key(0, 60))
    assert diff.added == ((_key(0, 72), 100), (_key(0, 76), 110))


def test_empty_previous_state_is_pure_addition_and_empty_current_is_pure_removal() -> None:
    assert diff_midi_states({}, {_key(0, 60): 100}) == MidiStateDiff(
        removed=(), added=((_key(0, 60), 100),)
    )
    assert diff_midi_states({_key(0, 60): 100}, {}) == MidiStateDiff(
        removed=(_key(0, 60),), added=()
    )


def test_key_type_is_free_for_flat_midi_keys() -> None:
    # The debug synth diffs flat channel*128+note keys, not MidiNoteKey
    # objects: the primitive must not special-case either representation.
    diff = diff_midi_states({0 * 128 + 60: 80, 1 * 128 + 64: 90}, {1 * 128 + 64: 90})
    assert diff.removed == (0 * 128 + 60,)
    assert diff.added == ()
