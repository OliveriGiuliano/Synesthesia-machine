"""Test callback output without opening a physical PortAudio device."""

import numpy as np
from tools.audio_probe import SineCallback


def test_preallocated_sine_callback_outputs_bounded_stereo() -> None:
    callback = SineCallback(sample_rate=48_000.0, frequency_hz=440.0, amplitude=0.05)
    callback.prepare(256)
    output = np.empty((256, 2), dtype=np.float32)

    callback(output, 256, object(), object())

    assert output.dtype == np.float32
    assert np.array_equal(output[:, 0], output[:, 1])
    assert float(np.max(np.abs(output))) <= 0.05
    assert np.count_nonzero(output) > 0
