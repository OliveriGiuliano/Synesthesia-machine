"""Headless tests for the typed source-configuration derivation.

The builders turn a source node's coerced parameter mapping into a typed
config value. They are pure: no engine, no I/O, no decoding, so the source
configuration can be exercised (and a parameter rename or retyping caught)
without spawning an engine.
"""

from __future__ import annotations

import pytest

from synesthesia_machine.media import (
    CameraBackendPreference,
    build_camera_source_config,
    build_video_source_config,
)


def test_build_video_source_config_maps_every_declared_parameter() -> None:
    config = build_video_source_config(
        {
            "file_path": "/videos/clip.mp4",
            "playback_speed": 1.5,
            "process_every_nth_frame": 2,
            "loop": True,
            "stream_index": 1,
        }
    )

    assert config.file_path == "/videos/clip.mp4"
    assert config.playback_speed == 1.5
    assert config.process_every_nth_frame == 2
    assert config.loop is True
    assert config.stream_index == 1
    # Omitted loop timestamps default to "video start" / "video end".
    assert config.loop_start_s == 0.0
    assert config.loop_end_s == 0.0


def test_build_camera_source_config_maps_parameter_and_converts_backend() -> None:
    config = build_camera_source_config(
        {
            "device_id": "opencv:1",
            "requested_width": 640,
            "requested_height": 480,
            "requested_fps": 25.0,
            "backend_preference": "V4L2",
            "process_every_nth_frame": 1,
            "reconnect_automatically": False,
        }
    )

    assert config.device_id == "opencv:1"
    assert config.requested_width == 640
    assert config.requested_height == 480
    assert config.requested_fps == 25.0
    assert config.backend_preference is CameraBackendPreference.V4L2
    assert config.process_every_nth_frame == 1
    assert config.reconnect_automatically is False


def test_build_video_source_config_rejects_mistyped_parameter() -> None:
    with pytest.raises(TypeError, match="file_path"):
        build_video_source_config(
            {
                "file_path": 12,
                "playback_speed": 1.0,
                "process_every_nth_frame": 1,
                "loop": False,
                "stream_index": 0,
            }
        )


def test_build_camera_source_config_rejects_bool_as_int() -> None:
    with pytest.raises(TypeError, match="requested_width"):
        build_camera_source_config(
            {
                "device_id": "opencv:0",
                "requested_width": True,
                "requested_height": 480,
                "requested_fps": 30.0,
                "backend_preference": "AUTO",
                "process_every_nth_frame": 1,
                "reconnect_automatically": True,
            }
        )
