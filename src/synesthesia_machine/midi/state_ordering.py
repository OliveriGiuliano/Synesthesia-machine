"""State-ordering policy for panic-capable MIDI sinks (ADR-0022).

One owner for the rejection rule every panic-capable sink applies (the MIDI
output service, the debug synth, and their null stand-ins), so a future
panic-capable sink inherits correct ordering for free:

* ``record_panic(publish_generation)`` raises the watermark to the generation
  of the most recent panic. A generation-0 panic (a reset, a shutdown)
  silences the sink without invalidating any engine-ordered state.
* A state frame is stale when it was ordered by the engine (its
  ``publish_generation`` is greater than zero) at or below that watermark: it
  was produced by a tick that lost the race against the panic, and applying
  it would re-arm exactly the notes or voices the panic silenced.
* Generation-0 states are source-built or directly scheduled, carry no tick
  ordering, and are never stale, whatever the watermark.

The policy compares publish generations only; no clock participates in the
ordering, so tests drive the race with explicit generations. It is not
internally synchronized: each sink applies it under the same discipline its
publish/panic paths always used (the synth's publisher lock; the MIDI
service records under its condition lock and reads the monotone watermark
plainly, as before).
"""

from __future__ import annotations


class StateOrdering:
    """Reject engine-ordered states at or below the most recent panic."""

    __slots__ = ("_stale_upto_generation",)

    def __init__(self) -> None:
        self._stale_upto_generation = 0

    @property
    def stale_upto_generation(self) -> int:
        """Highest publish generation invalidated by a panic so far."""

        return self._stale_upto_generation

    def record_panic(self, publish_generation: int) -> None:
        """Raise the watermark to a panic issued with ``publish_generation``."""

        if publish_generation < 0:
            msg = "publish_generation cannot be negative"
            raise ValueError(msg)
        self._stale_upto_generation = max(self._stale_upto_generation, publish_generation)

    def is_stale(self, publish_generation: int) -> bool:
        """True when a state carrying ``publish_generation`` must be rejected."""

        return 0 < publish_generation <= self._stale_upto_generation


__all__ = ["StateOrdering"]
