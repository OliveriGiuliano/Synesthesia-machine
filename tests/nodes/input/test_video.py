"""Pure-loop-editor tests for the Load Video definition (ADR-0023)."""

from __future__ import annotations

from uuid import UUID

from synesthesia_machine.contracts import SourceState, SourceStatus
from synesthesia_machine.nodes.input import create_input_definitions
from synesthesia_machine.nodes.input.video import LOAD_VIDEO_TYPE_ID

NODE_ID = UUID("00000000-0000-0000-0000-000000005070")


def _definition():
    return next(
        definition
        for definition in create_input_definitions()
        if definition.type_id is LOAD_VIDEO_TYPE_ID
    )


def _status(file_path: str = "/videos/a.mp4", duration_s: float | None = 120.5) -> SourceStatus:
    return SourceStatus(
        NODE_ID,
        SourceState.READY,
        file_path=file_path,
        duration_s=duration_s,
    )


def _resolve(parameter_id: str, values: dict[str, object], status: SourceStatus | None):
    definition = _definition()
    spec = definition.parameter(parameter_id)
    assert spec is not None
    resolved = definition.parameter_editor_resolver(spec, values, status)
    assert resolved is not None
    return resolved


def test_loop_editors_are_bounded_by_published_duration() -> None:
    values: dict[str, object] = {"file_path": "/videos/a.mp4"}
    for parameter_id in ("loop_start_s", "loop_end_s"):
        resolved = _resolve(parameter_id, values, _status())
        assert resolved.maximum == 120.5


def test_resolver_binds_both_loop_editors_to_the_full_range() -> None:
    # The mutual ordering of the two timestamps is enforced by the dual-knob
    # editor widget, not by the resolver: both editors get the full range.
    values: dict[str, object] = {"file_path": "/videos/a.mp4", "loop_end_s": 30.0}
    for parameter_id in ("loop_start_s", "loop_end_s"):
        resolved = _resolve(parameter_id, values, _status())
        assert resolved.maximum == 120.5


def test_editors_stay_unbounded_without_a_matching_status() -> None:
    values: dict[str, object] = {"file_path": "/videos/a.mp4"}
    spec = _definition().parameter("loop_start_s")
    assert spec is not None

    resolved = _definition().parameter_editor_resolver(spec, values, None)
    assert resolved is None or resolved is spec or resolved.maximum is None

    # A status for a different file must not bound this node's editors.
    resolved = _resolve("loop_start_s", values, _status(file_path="/videos/other.mp4"))
    assert resolved is spec or resolved.maximum is None

    # A status without a duration keeps the editors unbounded.
    resolved = _resolve("loop_start_s", values, _status(duration_s=None))
    assert resolved is spec or resolved.maximum is None


def test_editors_stay_unbounded_without_a_configured_file() -> None:
    values: dict[str, object] = {}
    resolved = _resolve("loop_start_s", values, _status())
    assert resolved is spec_without_file() or resolved.maximum is None


def spec_without_file():
    return _definition().parameter("loop_start_s")


def test_resolver_is_pure_and_never_probes_the_filesystem() -> None:
    # The status claims a file that does not exist; a pure resolver returns the
    # published duration without touching the filesystem.
    values: dict[str, object] = {"file_path": "/videos/missing.mp4"}
    resolved = _resolve(
        "loop_end_s",
        values,
        _status(file_path="/videos/missing.mp4", duration_s=60.0),
    )
    assert resolved.maximum == 60.0


def test_non_loop_parameters_are_untouched_by_the_resolver() -> None:
    definition = _definition()
    spec = definition.parameter("playback_speed")
    assert spec is not None
    resolved = definition.parameter_editor_resolver(spec, {"file_path": "/videos/a.mp4"}, _status())
    assert resolved is None or resolved is spec or resolved.minimum == spec.minimum
