"""Bounded per-node runtime timing and output diagnostics."""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from synesthesia_machine.contracts.engine_client import NodeProfile
from synesthesia_machine.contracts.runtime_values import (
    ChannelFrame,
    ColorValue,
    ImageFrame,
    MidiStateFrame,
    NoData,
    RuntimeValue,
    ValueArray,
)

DEFAULT_PROFILE_WINDOW = 240
DEFAULT_EMA_ALPHA = 0.2


@dataclass(frozen=True, slots=True)
class _ImageFact:
    height: int
    width: int
    channels: int
    dtype: object


@dataclass(frozen=True, slots=True)
class _ChannelFact:
    height: int
    width: int
    dtype: object


@dataclass(frozen=True, slots=True)
class _MidiFact:
    note_count: int


@dataclass(frozen=True, slots=True)
class _ColorFact:
    r: float
    g: float
    b: float
    a: float


@dataclass(frozen=True, slots=True)
class _BoolFact:
    value: bool


@dataclass(frozen=True, slots=True)
class _IntFact:
    value: int


@dataclass(frozen=True, slots=True)
class _FloatFact:
    value: float


@dataclass(frozen=True, slots=True)
class _StrFact:
    value: str


@dataclass(frozen=True, slots=True)
class _ArrayFact:
    item_type: str
    item_count: int


type _OutputFact = (
    _ImageFact
    | _ChannelFact
    | _MidiFact
    | _ColorFact
    | _BoolFact
    | _IntFact
    | _FloatFact
    | _StrFact
    | _ArrayFact
    | None
)
type _OutputFacts = tuple[tuple[str, _OutputFact, int], ...]


@dataclass(slots=True)
class _NodeAccumulator:
    durations_ns: deque[int]
    invocation_count: int = 0
    error_count: int = 0
    ema_ns: float = 0.0
    output_facts: _OutputFacts = ()
    output_bytes: int = 0


class RuntimeProfiler:
    """Thread-safe rolling metrics with bounded memory independent of run duration."""

    def __init__(
        self,
        *,
        window_capacity: int = DEFAULT_PROFILE_WINDOW,
        ema_alpha: float = DEFAULT_EMA_ALPHA,
    ) -> None:
        if window_capacity < 1:
            raise ValueError("Profiler window capacity must be positive")
        if not math.isfinite(ema_alpha) or not 0.0 < ema_alpha <= 1.0:
            raise ValueError("Profiler EMA alpha must be in the range (0, 1]")
        self.window_capacity = window_capacity
        self.ema_alpha = ema_alpha
        self._nodes: dict[UUID, _NodeAccumulator] = {}
        self._all_durations_ns: deque[int] = deque(maxlen=window_capacity * 16)
        self._lock = threading.Lock()

    def record(
        self,
        node_id: UUID,
        duration_ns: int,
        outputs: Mapping[str, RuntimeValue],
        failed: bool,
    ) -> None:
        if duration_ns < 0:
            raise ValueError("Profile duration cannot be negative")
        # Collect only cheap facts on the tick path: summary strings are
        # formatted from these facts when profiles() is read (UI cadence),
        # so an enabled profiler does no per-tick string building.
        output_facts, output_bytes = collect_output_facts(outputs)
        with self._lock:
            accumulator = self._nodes.get(node_id)
            if accumulator is None:
                accumulator = _NodeAccumulator(deque(maxlen=self.window_capacity))
                self._nodes[node_id] = accumulator
            accumulator.durations_ns.append(duration_ns)
            accumulator.invocation_count += 1
            accumulator.error_count += int(failed)
            accumulator.ema_ns = (
                float(duration_ns)
                if accumulator.invocation_count == 1
                else self.ema_alpha * duration_ns + (1.0 - self.ema_alpha) * accumulator.ema_ns
            )
            accumulator.output_facts = output_facts
            accumulator.output_bytes = output_bytes
            self._all_durations_ns.append(duration_ns)

    def profiles(self) -> tuple[NodeProfile, ...]:
        with self._lock:
            snapshots = {
                node_id: (
                    tuple(accumulator.durations_ns),
                    accumulator.invocation_count,
                    accumulator.error_count,
                    accumulator.ema_ns,
                    accumulator.output_facts,
                    accumulator.output_bytes,
                )
                for node_id, accumulator in self._nodes.items()
            }
        profiles: list[NodeProfile] = []
        for node_id, snapshot in snapshots.items():
            durations, invocation_count, error_count, ema_ns, output_facts, output_bytes = snapshot
            summary, _ = format_output_facts(output_facts)
            ordered = tuple(sorted(durations))
            profiles.append(
                NodeProfile(
                    node_id=node_id,
                    invocation_count=invocation_count,
                    error_count=error_count,
                    window_size=len(ordered),
                    last_duration_ms=durations[-1] / 1_000_000.0,
                    ema_duration_ms=ema_ns / 1_000_000.0,
                    p50_duration_ms=_percentile(ordered, 50.0) / 1_000_000.0,
                    p95_duration_ms=_percentile(ordered, 95.0) / 1_000_000.0,
                    max_duration_ms=ordered[-1] / 1_000_000.0,
                    output_summary=summary,
                    output_bytes=output_bytes,
                )
            )
        return tuple(sorted(profiles, key=lambda item: str(item.node_id)))

    def global_p95_ms(self) -> float:
        # Copy under the lock, sort outside it: the UI polls this while the
        # tick thread records, and holding the lock across a full-deque sort
        # could stall a tick mid-execution on a small machine.
        with self._lock:
            durations = tuple(self._all_durations_ns)
        if not durations:
            return 0.0
        ordered = tuple(sorted(durations))
        return _percentile(ordered, 95.0) / 1_000_000.0

    def reset(self) -> None:
        with self._lock:
            self._nodes.clear()
            self._all_durations_ns.clear()


def summarize_outputs(outputs: Mapping[str, RuntimeValue]) -> tuple[str, int]:
    """Compatibility helper: collect facts and format the display summary."""

    facts, total_bytes = collect_output_facts(outputs)
    return format_output_facts(facts, total_bytes)


def collect_output_facts(
    outputs: Mapping[str, RuntimeValue],
) -> tuple[_OutputFacts, int]:
    """Collect cheap per-output facts (no string building, no per-item format).

    Runs on the tick path while profiling is enabled, so it only performs the
    isinstance ladder and shape/dtype reads; summary text is built later by
    :func:`format_output_facts` at UI cadence.
    """

    facts: list[tuple[str, _OutputFact, int]] = []
    total_bytes = 0
    for port_id, value in sorted(outputs.items()):
        fact, value_bytes = _collect_value_facts(value)
        facts.append((port_id, fact, value_bytes))
        total_bytes += value_bytes
    return (tuple(facts), total_bytes)


def format_output_facts(facts: _OutputFacts, total_bytes: int | None = None) -> tuple[str, int]:
    """Build the display summary from collected facts (off the tick path)."""

    if total_bytes is None:
        total_bytes = sum(bytes for _, _, bytes in facts)
    if not facts:
        return "no outputs", total_bytes
    summaries = [f"{port_id}={_format_fact(fact)}" for port_id, fact, _ in facts]
    return (", ".join(summaries), total_bytes)


def _collect_value_facts(value: RuntimeValue) -> tuple[_OutputFact, int]:
    if value is NoData:
        return (None, 0)
    if isinstance(value, ImageFrame):
        height, width, channels = value.data.shape
        return (_ImageFact(height, width, channels, value.data.dtype), value.data.nbytes)
    if isinstance(value, ChannelFrame):
        height, width = value.data.shape
        return (_ChannelFact(height, width, value.data.dtype), value.data.nbytes)
    if isinstance(value, MidiStateFrame):
        return (_MidiFact(len(value.notes)), len(value.notes) * 3)
    if isinstance(value, ColorValue):
        return (_ColorFact(value.r, value.g, value.b, value.a), 32)
    if isinstance(value, bool):
        return (_BoolFact(value), 1)
    if isinstance(value, int):
        return (_IntFact(value), 8)
    if isinstance(value, float):
        return (_FloatFact(value), 8)
    if isinstance(value, str):
        return (_StrFact(value), len(value.encode("utf-8")))
    if isinstance(value, ValueArray):
        byte_count = sum(_collect_value_facts(item)[1] for item in value.values)
        return (_ArrayFact(value.item_type.value, len(value.values)), byte_count)
    raise TypeError(f"unsupported runtime value: {type(value).__name__}")


def _format_fact(fact: _OutputFact) -> str:
    if fact is None:
        return "NoData"
    if isinstance(fact, _ImageFact):
        return f"IMAGE {fact.width}x{fact.height}x{fact.channels} {fact.dtype}"
    if isinstance(fact, _ChannelFact):
        return f"CHANNEL {fact.width}x{fact.height} {fact.dtype}"
    if isinstance(fact, _MidiFact):
        return f"MIDI_STATE {fact.note_count} note(s)"
    if isinstance(fact, _ColorFact):
        return f"COLOR ({fact.r:.3g}, {fact.g:.3g}, {fact.b:.3g}, {fact.a:.3g})"
    if isinstance(fact, _BoolFact):
        return f"BOOL {fact.value}"
    if isinstance(fact, _IntFact):
        return f"INT {fact.value}"
    if isinstance(fact, _FloatFact):
        return f"FLOAT {fact.value:.8g}"
    if isinstance(fact, _StrFact):
        shown = fact.value if len(fact.value) <= 48 else fact.value[:45] + chr(8230)
        return f"STRING {shown!r}"
    return f"{fact.item_type}_ARRAY {fact.item_count} item(s)"


def _percentile(ordered: tuple[int, ...], percentile: float) -> float:
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


__all__ = [
    "DEFAULT_EMA_ALPHA",
    "DEFAULT_PROFILE_WINDOW",
    "RuntimeProfiler",
    "summarize_outputs",
]
