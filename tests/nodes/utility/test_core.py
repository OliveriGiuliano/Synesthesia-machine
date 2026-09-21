"""Number node output semantics and the v2-to-v3 migration."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts import FrameContext, ParameterValue
from synesthesia_machine.nodes.utility.core import (
    NumberRuntime,
    migrate_number_v1_to_v2,
    migrate_number_v2_to_v3,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000630")
SOURCE = UUID("00000000-0000-0000-0000-000000000631")

_CONTEXT = FrameContext(CLOCK, 1, 0, 0.0, 1, None, False)


def _process(parameters: Mapping[str, ParameterValue]) -> Mapping[str, object]:
    runtime = NumberRuntime(SOURCE)
    return runtime.process({}, parameters, _CONTEXT)


def test_number_float_output_keeps_the_value() -> None:
    assert _process({"value": 2.5})["value"] == 2.5
    assert _process({"value": -3.25})["value"] == -3.25


def test_number_int_output_truncates_toward_zero() -> None:
    assert _process({"value": 2.7})["int_value"] == 2
    assert _process({"value": -1.7})["int_value"] == -1
    assert _process({"value": 4.0})["int_value"] == 4


def test_both_outputs_are_published_for_every_value() -> None:
    outputs = _process({"value": 2.5})
    assert set(outputs) == {"value", "int_value"}


def test_v2_to_v3_migration_drops_the_type_selection() -> None:
    migrated = migrate_number_v2_to_v3(
        {
            "implementation_version": 2,
            "parameters": {"value": 4.0, "number_type": "INT"},
        }
    )

    # Both v3 outputs are always available, so the type choice is redundant.
    assert migrated["implementation_version"] == 3
    assert migrated["parameters"] == {"value": 4.0}

    already = migrate_number_v2_to_v3(
        {
            "implementation_version": 2,
            "parameters": {"value": 2.5},
        }
    )
    assert already["parameters"] == {"value": 2.5}


def test_v1_to_v2_migration_folds_the_type_selected_value() -> None:
    migrated = migrate_number_v1_to_v2(
        {
            "implementation_version": 1,
            "parameters": {
                "number_type": "INT",
                "float_value": 9.5,
                "int_value": 4,
            },
        }
    )

    # The v1 runtime ignored float_value in INT mode, so only int_value
    # may survive the fold.
    assert migrated["implementation_version"] == 2
    assert migrated["parameters"] == {"number_type": "INT", "value": 4.0}


def test_v1_to_v2_migration_omits_an_unstored_default_value() -> None:
    # An absent field means the v1 default (0), which equals the v2 default,
    # so nothing is stored. A stored field is folded verbatim.
    omitted = migrate_number_v1_to_v2(
        {
            "implementation_version": 1,
            "parameters": {"number_type": "FLOAT"},
        }
    )
    assert omitted["parameters"] == {"number_type": "FLOAT"}

    stored = migrate_number_v1_to_v2(
        {
            "implementation_version": 1,
            "parameters": {"number_type": "FLOAT", "float_value": 0.0},
        }
    )
    assert stored["parameters"] == {"number_type": "FLOAT", "value": 0.0}


def test_v1_to_v2_migration_without_type_selects_the_float_default() -> None:
    # A v1 payload that set int_value but never chose Integer still emitted
    # the FLOAT default (0.0); the migration must not promote int_value.
    migrated = migrate_number_v1_to_v2(
        {
            "implementation_version": 1,
            "parameters": {"int_value": 3},
        }
    )

    assert migrated["implementation_version"] == 2
    assert migrated["parameters"] == {}
