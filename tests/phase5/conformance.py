"""Reusable Phase 5 node conformance assertions."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid5

import numpy as np

from synesthesia_machine.contracts import ImageFrame, NoData, PortType
from synesthesia_machine.nodes import NodeDefinition
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)
from tests.phase1.helpers import frame_context

_EXTERNAL_SOURCE = UUID("00000000-0000-0000-0000-000000005001")
_DOCUMENT = UUID("00000000-0000-0000-0000-000000005002")


def assert_image_conformance(
    source: ImageFrame,
    output: ImageFrame,
    before: np.ndarray[tuple[int, ...], np.dtype[np.float32]],
    *,
    expected_shape: tuple[int, int, int],
) -> None:
    assert output.data.dtype == np.float32
    assert output.data.shape == expected_shape
    assert output.data.flags.c_contiguous
    assert not output.data.flags.writeable
    assert output.context is source.context
    assert output.provenance is source.provenance
    assert output.color_space is source.color_space
    assert output.channel_names == source.channel_names
    assert output.alpha_mode is source.alpha_mode
    assert np.array_equal(source.data, before, equal_nan=True)
    assert not source.data.flags.writeable


def assert_scheduler_propagates_no_data(
    definition: NodeDefinition,
    parameters: Mapping[str, object],
) -> None:
    values, errors = definition.parameter_values(parameters)
    assert not errors
    node_id = uuid5(_DOCUMENT, definition.type_id)
    input_types = {port.id: PortType.IMAGE for port in definition.inputs}
    output_types = {port.id: PortType.IMAGE for port in definition.outputs}
    compiled = CompiledNode(
        node_id,
        definition,
        parameters=values,
        input_bindings={"image": InputBinding(PortKey(_EXTERNAL_SOURCE, "image"))},
        input_types=input_types,
        output_types=output_types,
        clock_id=_EXTERNAL_SOURCE,
        is_static=False,
    )
    scheduler = Scheduler(ExecutionPlan(_DOCUMENT, 1, (compiled,), frozenset({node_id})))
    try:
        result = scheduler.execute_tick(frame_context(clock_id=_EXTERNAL_SOURCE))
    finally:
        scheduler.close()
    assert result.errors == ()
    assert all(result.values[PortKey(node_id, port_id)] is NoData for port_id in output_types)


__all__ = ["assert_image_conformance", "assert_scheduler_propagates_no_data"]
