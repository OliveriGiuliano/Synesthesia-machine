"""Typed source-configuration values for source nodes.

A source node's configuration (a video file's path and playback settings, a
camera's device and capture policy) travels as one of these frozen values
rather than as raw parameter-name strings re-read at every call site. The
``build_*_source_config`` helpers derive the value from a node's coerced
parameter mapping in a single place, so adding or renaming a source parameter
updates the node definition and one builder, not every engine and exporter
consumer. The old string-keyed reads remain valid in parallel: the builders
still index the same parameter names, so existing raw-readers keep working.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from synesthesia_machine.media.camera_source import CameraBackendPreference

__all__ = [
    "CameraSourceConfig",
    "VideoSourceConfig",
    "build_camera_source_config",
    "build_video_source_config",
]


@dataclass(frozen=True, slots=True)
class VideoSourceConfig:
    """Typed configuration for one Load Video source node."""

    file_path: str
    playback_speed: float
    process_every_nth_frame: int
    loop: bool
    stream_index: int
    loop_start_s: float
    loop_end_s: float


@dataclass(frozen=True, slots=True)
class CameraSourceConfig:
    """Typed configuration for one Load Camera source node."""

    device_id: str
    requested_width: int
    requested_height: int
    requested_fps: float
    backend_preference: CameraBackendPreference
    process_every_nth_frame: int
    reconnect_automatically: bool


def build_video_source_config(params: Mapping[str, object]) -> VideoSourceConfig:
    """Derive the Load Video configuration from a node's parameter mapping.

    The mapping holds the node's coerced parameter values keyed by the
    parameter IDs declared in the node definition. Each value is type-checked
    against the parameter's declared type so a mismatched mapping fails fast
    instead of surfacing later inside the source service.
    """

    return VideoSourceConfig(
        file_path=_require_str(params, "file_path"),
        playback_speed=_require_float(params, "playback_speed"),
        process_every_nth_frame=_require_int(params, "process_every_nth_frame"),
        loop=_require_bool(params, "loop"),
        stream_index=_require_int(params, "stream_index"),
        # 0.0 means "video start" / "video end", so omitted timestamps fall
        # back to the same values the node definition declares.
        loop_start_s=_optional_float(params, "loop_start_s", 0.0),
        loop_end_s=_optional_float(params, "loop_end_s", 0.0),
    )


def build_camera_source_config(params: Mapping[str, object]) -> CameraSourceConfig:
    """Derive the Load Camera configuration from a node's parameter mapping."""

    return CameraSourceConfig(
        device_id=_require_str(params, "device_id"),
        requested_width=_require_int(params, "requested_width"),
        requested_height=_require_int(params, "requested_height"),
        requested_fps=_require_float(params, "requested_fps"),
        backend_preference=CameraBackendPreference(_require_str(params, "backend_preference")),
        process_every_nth_frame=_require_int(params, "process_every_nth_frame"),
        reconnect_automatically=_require_bool(params, "reconnect_automatically"),
    )


def _require_str(params: Mapping[str, object], name: str) -> str:
    value = params[name]
    if not isinstance(value, str):
        raise TypeError(f"Expected string source parameter {name!r}")
    return value


def _require_int(params: Mapping[str, object], name: str) -> int:
    value = params[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Expected integer source parameter {name!r}")
    return value


def _require_float(params: Mapping[str, object], name: str) -> float:
    value = params[name]
    if not isinstance(value, float):
        raise TypeError(f"Expected float source parameter {name!r}")
    return value


def _optional_float(params: Mapping[str, object], name: str, default: float) -> float:
    if name not in params:
        return default
    return _require_float(params, name)


def _require_bool(params: Mapping[str, object], name: str) -> bool:
    value = params[name]
    if not isinstance(value, bool):
        raise TypeError(f"Expected boolean source parameter {name!r}")
    return value
