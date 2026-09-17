"""Shared pure diff between two desired MIDI note states.

One set-difference serves every consumer that compares a previous desired
state with a current one: the MIDI output service (which layers its
velocity-update policy on top), the offline MIDI export (which projects
the diff to the on/off-only policy declared by ADR-0016/0019), and the
debug synth (which drives voice entry and exit from it).  A key present
in both states is unchanged, whatever its velocity: deciding whether a
velocity change of a held note matters is each caller's policy, not part
of the diff.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MidiStateDiff[Key]:
    """Keys leaving one desired state and notes entering the next.

    ``removed`` lists the keys present in the previous state and absent
    from the current one, in previous-state order.  ``added`` maps each
    key present in the current state and absent from the previous one to
    its current velocity, in current-state order.  Order is kept because
    the output service relies on it for deterministic message order.
    """

    removed: tuple[Key, ...]
    added: tuple[tuple[Key, int], ...]


def diff_midi_states[Key](
    previous: Mapping[Key, int] | Set[Key],
    current: Mapping[Key, int],
) -> MidiStateDiff[Key]:
    """Diff two desired note states into leaving keys and entering notes.

    ``previous`` contributes its keys only (a mapping of held notes or the
    bare key set, as the debug synth tracks them); ``current`` contributes
    keys and velocities.
    """
    current_keys = set(current)
    previous_keys = set(previous)
    # A mapping previous keeps its insertion order for deterministic caller
    # ordering; a bare key set has no order to keep.
    previous_order = previous.keys() if isinstance(previous, Mapping) else previous
    return MidiStateDiff(
        removed=tuple(key for key in previous_order if key not in current_keys),
        added=tuple(
            (key, velocity) for key, velocity in current.items() if key not in previous_keys
        ),
    )


__all__ = ["MidiStateDiff", "diff_midi_states"]
