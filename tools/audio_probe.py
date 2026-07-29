"""Produce a quiet 440 Hz sine via an allocation-free sounddevice callback."""

import argparse
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray


@dataclass(slots=True)
class SineCallback:
    """Stateful callback whose hot path performs no allocation, blocking, or logging."""

    sample_rate: float = 48_000.0
    frequency_hz: float = 440.0
    amplitude: float = 0.05
    phase: float = 0.0
    _phase_step: float = 0.0
    _phase_buffer: NDArray[np.float32] | None = None
    _mono_buffer: NDArray[np.float32] | None = None

    def prepare(self, block_size: int) -> None:
        if block_size <= 0:
            msg = "block_size must be positive"
            raise ValueError(msg)
        self._phase_step = 2.0 * np.pi * self.frequency_hz / self.sample_rate
        self._phase_buffer = np.arange(block_size, dtype=np.float32)
        self._mono_buffer = np.empty(block_size, dtype=np.float32)

    def __call__(
        self,
        outdata: NDArray[np.float32],
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        del time_info, status
        phase_buffer = self._phase_buffer
        mono_buffer = self._mono_buffer
        if phase_buffer is None or mono_buffer is None or frames > len(phase_buffer):
            outdata.fill(0.0)
            return

        np.multiply(phase_buffer[:frames], self._phase_step, out=mono_buffer[:frames])
        np.add(mono_buffer[:frames], self.phase, out=mono_buffer[:frames])
        np.sin(mono_buffer[:frames], out=mono_buffer[:frames])
        np.multiply(mono_buffer[:frames], self.amplitude, out=mono_buffer[:frames])
        outdata[:frames, 0] = mono_buffer[:frames]
        outdata[:frames, 1] = mono_buffer[:frames]
        self.phase = (self.phase + frames * self._phase_step) % (2.0 * np.pi)


def play_sine(*, duration_seconds: float = 1.0, sample_rate: int = 48_000) -> None:
    """Open the default output only when explicitly called by the CLI."""

    block_size = 256
    callback = SineCallback(sample_rate=float(sample_rate))
    callback.prepare(block_size)
    with sd.OutputStream(
        samplerate=sample_rate,
        blocksize=block_size,
        channels=2,
        dtype="float32",
        callback=callback,
    ):
        time.sleep(duration_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()
    if not args.play:
        parser.error("audio output is opt-in; use --play")
    play_sine()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
