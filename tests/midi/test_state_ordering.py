"""Unit tests for the shared state-ordering policy (ADR-0022).

The policy is driven with explicit publish generations only - no clock -
because the generation comparison, never a wall-clock instant, is what
orders engine-ordered state against panics.
"""

from __future__ import annotations

import pytest

from synesthesia_machine.midi.state_ordering import StateOrdering


def test_panic_watermark_rejects_states_at_or_below_it_and_admits_newer_ones() -> None:
    ordering = StateOrdering()
    assert not ordering.is_stale(1)
    ordering.record_panic(1)
    assert ordering.is_stale(1)
    assert not ordering.is_stale(2)
    ordering.record_panic(3)
    assert ordering.stale_upto_generation == 3
    assert ordering.is_stale(3)
    assert not ordering.is_stale(4)


def test_watermark_is_monotone_and_lower_or_zero_panics_filter_nothing() -> None:
    ordering = StateOrdering()
    ordering.record_panic(5)
    # A later panic with a lower generation must not lower the watermark:
    # a state ordered after the higher panic is still stale.
    ordering.record_panic(2)
    assert ordering.stale_upto_generation == 5
    # A generation-0 panic (a reset, a shutdown) silences the sink without
    # filtering later, newer states.
    ordering.record_panic(0)
    assert ordering.stale_upto_generation == 5
    assert ordering.is_stale(5)
    assert not ordering.is_stale(6)


def test_generation_zero_states_are_never_stale() -> None:
    # Source-built and directly scheduled states carry no tick ordering and
    # must keep flowing no matter how deep the panic watermark gets.
    ordering = StateOrdering()
    ordering.record_panic(42)
    assert not ordering.is_stale(0)


def test_negative_panic_generation_is_rejected() -> None:
    ordering = StateOrdering()
    with pytest.raises(ValueError, match="publish_generation"):
        ordering.record_panic(-1)
    assert ordering.stale_upto_generation == 0
