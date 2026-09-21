"""Number node output semantics and the v3-to-v4 migration."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from synesthesia_machine.contracts import FrameContext, ParameterValue
from synesthesia_machine.nodes.utility.core import (
    NumberRuntime,
    migrate_number_v1_to_v2,
    migrate_number_v3_to_v4,
)

CLOCK = UUID("00000000-0000-0000-0000-000000000630")
SOURCE = UUID("00000000-0000-0000-0000-000000000631")

_CONTEXT = FrameContext(CLOCK, 1, 0, 0.0, 1, None, False)


def _process(parameters: Mapping[str, ParameterValue]) -> object:
    runtime = NumberRuntime(SOURCE)
    return runtime.process({}, parameters, _CONTEXT)["value"]


def test_whole_numbers_are_emitted_as_integers() -> None:
    # A v4 output settles to Integer when its context requires it, so the
    # runtime emits whole literals as ints that fill that declaration.
    assert _process({"value": 4.0}) == 4
    assert _process({"value": -1.0}) == -1
    assert _process({"value": 0.0}) == 0
    assert isinstance(_process({"value": 4.0}), int)


def test_fractional_values_stay_floats() -> None:
    # An Integer context rejects these at the output boundary instead of
    # truncating; a Float context receives them unchanged.
    assert _process({"value": 2.7}) == 2.7
    assert _process({"value": -1.7}) == -1.7
    assert isinstance(_process({"value": 2.7}), float)


def test_v3_to_v4_migration_is_payload_identity() -> None:
    migrated = migrate_number_v3_to_v4(
        {
            "implementation_version": 3,
            "parameters": {"value": 4.0},
        }
    )

    # v4 changes only which output the value feeds, not what is stored.
    assert migrated["implementation_version"] == 4
    assert migrated["parameters"] == {"value": 4.0}


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
