"""Number node conversion semantics and v1-to-v2 migration selection rules."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts import FrameContext, ParameterValue
from synesthesia_machine.nodes.utility.core import (
    NumberRuntime,
    migrate_number_v1_to_v2,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000630")
SOURCE = UUID("00000000-0000-0000-0000-000000000631")

_CONTEXT = FrameContext(CLOCK, 1, 0, 0.0, 1, None, False)


def _process(parameters: Mapping[str, ParameterValue]) -> object:
    runtime = NumberRuntime(SOURCE)
    return runtime.process({}, parameters, _CONTEXT)["value"]


def test_number_int_mode_truncates_toward_zero() -> None:
    assert _process({"number_type": "INT", "value": 2.7}) == 2
    assert _process({"number_type": "INT", "value": -1.7}) == -1
    assert _process({"number_type": "INT", "value": 4.0}) == 4


def test_number_float_mode_passes_the_value_through() -> None:
    assert _process({"number_type": "FLOAT", "value": 2.5}) == 2.5
    assert _process({"number_type": "FLOAT", "value": -3.25}) == -3.25


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
