"""Load Video source definition; lifecycle execution is owned by VideoSourceService."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from uuid import UUID

from synesthesia_machine.contracts import JsonObject, ParameterValue, PortType
from synesthesia_machine.media import build_video_source_config
from synesthesia_machine.media.video_source import inspect_video
from synesthesia_machine.media_path import normalize_media_path
from synesthesia_machine.nodes.base import (
    CachePolicy,
    ExecutionKind,
    NoDataRuntime,
    NodeDefinition,
    OutputPortSpec,
    ParameterEditorHint,
    ParameterSpec,
    ParameterUpdateMode,
    SourceOutputContract,
)
from synesthesia_machine.nodes.migrations import migration_parameters

LOAD_VIDEO_TYPE_ID = "synmachine.input.load_video"

_LOOP_TIMESTAMP_IDS = frozenset({"loop_start_s", "loop_end_s"})
_DURATION_CACHE_MAX_ENTRIES = 128
_video_duration_cache: dict[tuple[str, int, int], float | None] = {}


def migrate_load_video_v0_to_v1(data: JsonObject) -> JsonObject:
    migrated = deepcopy(data)
    parameters = migration_parameters(migrated)
    if "file_path" not in parameters and "path" in parameters:
        parameters["file_path"] = parameters.pop("path")
    migrated["implementation_version"] = 1
    return migrated


def migrate_load_video_v1_to_v2(data: JsonObject) -> JsonObject:
    """Load Video v2 adds the optional loop start/end timestamps (seconds)."""
    migrated = deepcopy(data)
    parameters = migration_parameters(migrated)
    parameters.setdefault("loop_start_s", 0.0)
    parameters.setdefault("loop_end_s", 0.0)
    migrated["implementation_version"] = 2
    return migrated


class LoadVideoRuntime(NoDataRuntime):
    """Safe scheduler placeholder for externally injected source outputs."""

    def __init__(self, node_id: UUID) -> None:
        super().__init__(node_id, ("image", "processed_index"))


def _video_region_duration(file_path: str, stream_index: int) -> float | None:
    """Cached playback duration for one video file/stream pair.

    The editor resolver calls this on every re-projection, so the container
    is only re-opened when the file's mtime (or the stream choice) changes.
    """

    try:
        source_path = normalize_media_path(file_path)
        key = (str(source_path), int(source_path.stat().st_mtime_ns), stream_index)
    except (OSError, ValueError):
        return None
    if key in _video_duration_cache:
        return _video_duration_cache[key]
    if len(_video_duration_cache) >= _DURATION_CACHE_MAX_ENTRIES:
        _video_duration_cache.clear()
    duration: float | None
    try:
        duration = inspect_video(source_path, stream_index=stream_index).duration_s
    except Exception:
        duration = None
    _video_duration_cache[key] = duration
    return duration


def _validate_load_video(parameters: Mapping[str, ParameterValue]) -> Sequence[str]:
    start = parameters.get("loop_start_s")
    end = parameters.get("loop_end_s")
    if isinstance(start, float) and isinstance(end, float) and end > 0.0 and start >= end:
        return ("loop end must be after loop start",)
    return ()


def _load_video_parameter_editor(
    spec: ParameterSpec, values: Mapping[str, ParameterValue]
) -> ParameterSpec:
    """Bound the loop timestamp editors to the video's real time range.

    With a resolvable file the sliders stop at the video's duration; the
    start timestamp additionally stops at a concrete loop end.
    """

    if spec.id not in _LOOP_TIMESTAMP_IDS:
        return spec
    file_path = values.get("file_path")
    stream_index = values.get("stream_index")
    if not isinstance(file_path, str) or not file_path or not isinstance(stream_index, int):
        return spec
    duration = _video_region_duration(file_path, stream_index)
    if duration is None:
        return spec
    maximum = float(duration)
    if spec.id == "loop_start_s":
        end = values.get("loop_end_s")
        if isinstance(end, float) and end > 0.0:
            maximum = min(maximum, end)
    if maximum <= 0.0:
        return spec
    return replace(spec, maximum=maximum)


def create_input_definitions() -> tuple[NodeDefinition, ...]:
    return (
        NodeDefinition(
            LOAD_VIDEO_TYPE_ID,
            2,
            "Load Video",
            "Input",
            "Plays a video file.",
            (),
            (
                OutputPortSpec("image", "Image", PortType.IMAGE),
                OutputPortSpec("processed_index", "Processed index", PortType.INT),
            ),
            (
                ParameterSpec(
                    "file_path",
                    "File path",
                    PortType.STRING,
                    "",
                    help_text="Path to a saved video file.",
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "playback_speed",
                    "Playback speed",
                    PortType.FLOAT,
                    1.0,
                    help_text=(
                        "Scale PTS playback timing from 0.25x to 4x; changing it restarts the "
                        "source."
                    ),
                    minimum=0.25,
                    maximum=4.0,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                    editor_hint=ParameterEditorHint.SLIDER,
                ),
                ParameterSpec(
                    "process_every_nth_frame",
                    "Process every Nth frame",
                    PortType.INT,
                    1,
                    minimum=1,
                    help_text="N=2 processes source frames 1, 3, 5… without slowing playback.",
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "loop",
                    "Loop",
                    PortType.BOOL,
                    False,
                    help_text=(
                        "When on, the played segment starts again from the loop start when it "
                        "reaches the loop end."
                    ),
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
                ParameterSpec(
                    "loop_start_s",
                    "Loop start",
                    PortType.FLOAT,
                    0.0,
                    help_text=(
                        "Where playback (and the loop) starts in the video, as a timestamp "
                        "such as 00:01:30. 00:00:00 is the beginning of the file."
                    ),
                    minimum=0.0,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                    editor_hint=ParameterEditorHint.TIMESTAMP,
                ),
                ParameterSpec(
                    "loop_end_s",
                    "Loop end",
                    PortType.FLOAT,
                    0.0,
                    help_text=(
                        "Where playback (and the loop) ends, as a timestamp such as 00:01:30. "
                        "00:00:00 means the end of the video; otherwise it must stay after the "
                        "loop start."
                    ),
                    minimum=0.0,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                    editor_hint=ParameterEditorHint.TIMESTAMP,
                ),
                ParameterSpec(
                    "stream_index",
                    "Video stream index",
                    PortType.INT,
                    0,
                    help_text="Index of the video stream inside the file; 0 is the first stream.",
                    minimum=0,
                    update_mode=ParameterUpdateMode.RESTART_SOURCE,
                ),
            ),
            ExecutionKind.SOURCE,
            LoadVideoRuntime,
            cache_policy=CachePolicy.NEVER,
            aliases=("video", "movie", "file video"),
            parameter_validator=_validate_load_video,
            parameter_editor_resolver=_load_video_parameter_editor,
            migrations={0: migrate_load_video_v0_to_v1, 1: migrate_load_video_v1_to_v2},
            media_parameter_id="file_path",
            source_outputs=SourceOutputContract("image", "processed_index"),
            source_config_builder=build_video_source_config,
        ),
    )


__all__ = ["LOAD_VIDEO_TYPE_ID", "LoadVideoRuntime", "create_input_definitions"]
