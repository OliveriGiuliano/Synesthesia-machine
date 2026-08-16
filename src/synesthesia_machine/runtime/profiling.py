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


@dataclass(slots=True)
class _NodeAccumulator:
    durations_ns: deque[int]
    invocation_count: int = 0
    error_count: int = 0
    ema_ns: float = 0.0
    output_summary: str = "no outputs"
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
        summary, output_bytes = summarize_outputs(outputs)
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
            accumulator.output_summary = summary
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
                    accumulator.output_summary,
                    accumulator.output_bytes,
                )
                for node_id, accumulator in self._nodes.items()
            }
        profiles: list[NodeProfile] = []
        for node_id, snapshot in snapshots.items():
            durations, invocation_count, error_count, ema_ns, summary, output_bytes = snapshot
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
        with self._lock:
            ordered = tuple(sorted(self._all_durations_ns))
        return _percentile(ordered, 95.0) / 1_000_000.0 if ordered else 0.0

    def reset(self) -> None:
        with self._lock:
            self._nodes.clear()
            self._all_durations_ns.clear()


def summarize_outputs(outputs: Mapping[str, RuntimeValue]) -> tuple[str, int]:
    summaries: list[str] = []
    total_bytes = 0
    for port_id, value in sorted(outputs.items()):
        summary, value_bytes = _summarize_value(value)
        summaries.append(f"{port_id}={summary}")
        total_bytes += value_bytes
    return (", ".join(summaries) if summaries else "no outputs", total_bytes)


def _summarize_value(value: RuntimeValue) -> tuple[str, int]:
    if value is NoData:
        return "NoData", 0
    if isinstance(value, ImageFrame):
        height, width, channels = value.data.shape
        return f"IMAGE {width}x{height}x{channels} {value.data.dtype}", value.data.nbytes
    if isinstance(value, ChannelFrame):
        height, width = value.data.shape
        return f"CHANNEL {width}x{height} {value.data.dtype}", value.data.nbytes
    if isinstance(value, MidiStateFrame):
        return f"MIDI_STATE {len(value.notes)} note(s)", len(value.notes) * 3
    if isinstance(value, ColorValue):
        return f"COLOR ({value.r:.3g}, {value.g:.3g}, {value.b:.3g}, {value.a:.3g})", 32
    if isinstance(value, bool):
        return f"BOOL {value}", 1
    if isinstance(value, int):
        return f"INT {value}", 8
    if isinstance(value, float):
        return f"FLOAT {value:.8g}", 8
    if isinstance(value, str):
        shown = value if len(value) <= 48 else value[:45] + "…"
        return f"STRING {shown!r}", len(value.encode("utf-8"))
    if isinstance(value, ValueArray):
        byte_count = sum(_summarize_value(item)[1] for item in value.values)
        return f"{value.item_type.value}_ARRAY {len(value.values)} item(s)", byte_count
    raise TypeError(f"unsupported runtime value: {type(value).__name__}")


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
