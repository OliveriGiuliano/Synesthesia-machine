"""Bounded native-thread configuration tests."""

from __future__ import annotations

import cv2
import psutil
import pytest

from synesthesia_machine.runtime.engine_server import (
    MAX_OPENCV_THREADS,
    configure_opencv_threads,
)


@pytest.mark.parametrize(
    ("physical", "logical", "expected"),
    ((24, 32, 16), (8, 16, 8), (None, 12, 12), (None, None, 1)),
)
def test_opencv_threads_are_bounded_by_physical_capacity(
    monkeypatch: pytest.MonkeyPatch,
    physical: int | None,
    logical: int | None,
    expected: int,
) -> None:
    selected: list[int] = []

    def cpu_count(*, logical: bool = True) -> int | None:
        return logical_count if logical else physical

    logical_count = logical
    monkeypatch.setattr(psutil, "cpu_count", cpu_count)
    monkeypatch.setattr(cv2, "setNumThreads", selected.append)

    assert configure_opencv_threads() == expected
    assert selected == [expected]
    assert expected <= MAX_OPENCV_THREADS
